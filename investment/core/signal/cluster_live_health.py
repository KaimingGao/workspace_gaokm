"""分池 live 健康：覆盖率 / 陈旧 / 重拟合提示（从 cluster_live 拆出）。"""


import logging

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


def _default_health_universe() -> List[str]:
    """优先纸面持仓（与分组宇宙一致），否则观察池。"""
    codes: List[str] = []
    try:
        from core.paper import load_paper
        from core.paths import PAPER_PATH

        paper = load_paper(PAPER_PATH)
        for h in paper.get("holdings") or []:
            if isinstance(h, dict) and h.get("stock_code"):
                codes.append(str(h["stock_code"]).strip())
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_live_health.py", exc_info=True)
        pass
    if len(codes) >= 2:
        return codes[:40]
    try:
        from core.watching.store import read_watching

        return list((read_watching().get("watchlist") or [])[:40])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_live_health.py", exc_info=True)
        return codes[:40]


def assess_cluster_live_health(
    *,
    universe: Optional[Sequence[str]] = None,
    compute_ic: Optional[bool] = None,
) -> Dict[str, Any]:
    """覆盖率 / 陈旧 / 模式门禁（L3）。

    ``compute_ic``：默认仅 ``mode=active`` 时算滚动 ŷ IC（shadow 刷簿/对照免等）。
    """
    from core.signal.cluster_live import get_cluster_scoring_cfg, load_active_cluster_weights

    cs = get_cluster_scoring_cfg()
    active = load_active_cluster_weights()
    alerts: List[str] = []
    mapped = set((active or {}).get("code_map") or {})
    if universe is None:
        universe = _default_health_universe()
    uni = [str(c).strip() for c in (universe or []) if str(c).strip()]
    hit = sum(1 for c in uni if c in mapped)
    coverage = (hit / len(uni)) if uni else None

    age_days = None
    stale = False
    if active and active.get("promoted_at"):
        try:
            promoted = datetime.strptime(
                str(active["promoted_at"]).replace("Z", ""), "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - promoted).total_seconds() / 86400.0
            if age_days > float(cs["max_age_days"]):
                stale = True
                alerts.append(
                    f"映射陈旧 {age_days:.1f}d > {cs['max_age_days']}d"
                )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_live_health.py", exc_info=True)
            pass

    if coverage is not None and coverage < float(cs["min_coverage"]):
        alerts.append(
            f"覆盖率 {coverage:.0%} < 阈值 {float(cs['min_coverage']):.0%}"
        )

    # Y1.2：映射拟合元数据（建议重拟合，不只 shadow 降级）
    fitted_as_of = None
    sample_count_min = None
    ridge_lambda = None
    refit_suggested = False
    try:
        clusters = list((active or {}).get("clusters") or [])
        samples = []
        ridges = []
        fitted_dates = []
        for cl in clusters:
            rm = (cl or {}).get("return_model") or {}
            if not isinstance(rm, dict):
                continue
            if rm.get("sample_count") is not None:
                try:
                    samples.append(int(rm["sample_count"]))
                except (TypeError, ValueError):
                    pass
            if rm.get("ridge_lambda") is not None:
                try:
                    ridges.append(float(rm["ridge_lambda"]))
                except (TypeError, ValueError):
                    pass
            fa = rm.get("fitted_as_of") or rm.get("as_of")
            if fa:
                fitted_dates.append(str(fa)[:10])
        if samples:
            sample_count_min = min(samples)
        if ridges:
            ridge_lambda = round(sum(ridges) / len(ridges), 6)
        if fitted_dates:
            fitted_as_of = max(fitted_dates)
        # 映射晋升超龄，或样本过少 → 建议重跑分组
        if stale or (sample_count_min is not None and sample_count_min < 24):
            refit_suggested = True
            if sample_count_min is not None and sample_count_min < 24:
                alerts.append(f"组模型最小样本 {sample_count_min} < 24 · 建议跑分组重拟合")
        elif age_days is not None and age_days > float(cs["max_age_days"]) * 0.7:
            refit_suggested = True
            alerts.append(
                f"映射年龄 {age_days:.1f}d 接近上限 · 建议研究枢纽跑分组→对照"
            )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_live_health.py", exc_info=True)
        pass

    # B4：滚动 ŷ IC（默认仅 active；对照刷簿跳过以免久等）
    ic_demote = False
    yhat_ic = None
    do_ic = bool(compute_ic) if compute_ic is not None else (cs.get("mode") == "active")
    if do_ic:
        try:
            min_ic = float(cs.get("min_yhat_rolling_ic") or 0.0)
            block_ic = bool(cs.get("block_active_on_yhat_ic", True))
            from core.strategy_monitor import estimate_rolling_yhat_ic_for_codes

            ic_codes = list(mapped)[:12] if mapped else []
            pack = (
                estimate_rolling_yhat_ic_for_codes(ic_codes, limit=4) if ic_codes else {}
            )
            yhat_ic = pack.get("rolling_ic") if isinstance(pack, dict) else None
            if (
                block_ic
                and yhat_ic is not None
                and isinstance(yhat_ic, (int, float))
                and float(yhat_ic) < min_ic
            ):
                ic_demote = True
                refit_suggested = True
                alerts.append(
                    f"滚动 ŷ IC={float(yhat_ic):.3f} < 阈值 {min_ic} · 建议降级/重估"
                )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_live_health.py", exc_info=True)
            pass

    mode = cs["mode"]
    demoted = False
    if mode == "active" and (
        stale
        or ic_demote
        or (coverage is not None and coverage < cs["min_coverage"])
    ):
        alerts.append("建议降级：active 条件不满足（请改 shadow/off）")
        if cs.get("auto_demote_on_stale") and (stale or ic_demote):
            demoted = True

    allow_active = (
        bool(active)
        and not stale
        and not ic_demote
        and (coverage is None or coverage >= float(cs["min_coverage"]))
        and (sample_count_min is None or sample_count_min >= 24)
    )

    # FM2 · IC 衰减摘要（滚动 IC / demote 状态；完整 lag 曲线见 walk_forward.ic_decay_curve）
    ic_decay_summary: Dict[str, Any] = {
        "ok": True,
        "track": "FM2",
        "ic_demote": bool(ic_demote),
        "refit_suggested": bool(refit_suggested),
        "stale": bool(stale),
        "yhat_rolling_ic": yhat_ic if not isinstance(yhat_ic, dict) else yhat_ic.get("ic"),
        "note": "衰减触发与 B4 refit/demote 同源；完整 lag 曲线在研究 WF 报告",
    }
    if isinstance(yhat_ic, dict):
        series = yhat_ic.get("series") or yhat_ic.get("ic_series")
        if isinstance(series, (list, tuple)) and series:
            try:
                vals = [float(x) for x in series if x is not None]
                if vals:
                    ic_decay_summary["series_n"] = len(vals)
                    ic_decay_summary["series_mean"] = round(sum(vals) / len(vals), 4)
                    ic_decay_summary["series_last"] = round(vals[-1], 4)
                    if len(vals) >= 2:
                        ic_decay_summary["series_delta"] = round(vals[-1] - vals[0], 4)
            except (TypeError, ValueError):
                pass

    return {
        "success": True,
        "task": "cluster_live_health",
        "mode": mode,
        "enabled": cs["enabled"],
        "has_active": bool(active),
        "version": (active or {}).get("version"),
        "promoted_at": (active or {}).get("promoted_at"),
        "n_mapped_codes": (active or {}).get("n_mapped_codes"),
        "universe_size": len(uni),
        "mapped_in_universe": hit,
        "coverage": round(coverage, 3) if coverage is not None else None,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "stale": stale,
        "ic_demote": ic_demote,
        "yhat_rolling_ic": yhat_ic,
        "ic_decay_summary": ic_decay_summary,
        "ic_computed": do_ic,
        "fitted_as_of": fitted_as_of,
        "sample_count_min": sample_count_min,
        "ridge_lambda": ridge_lambda,
        "refit_suggested": refit_suggested,
        "allow_active": allow_active,
        "suggest_demote": demoted or (mode == "active" and not allow_active),
        "alerts": alerts,
        "signal_config_touched": False,
        "track": "B4+FM2",
    }
