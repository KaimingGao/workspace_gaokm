"""横截面批量打分：组合回测与 rank_cross_section 共用（P49）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Tuple

from core.signal.config import load_signal_config
from core.signal.neutralize import apply_cross_section_neutralization
from core.signal.scorer import score_bars


def rank_scored_items(
    items: List[dict],
    *,
    min_score: float,
    score_key: str = "score",
) -> List[Tuple[str, float]]:
    picks: List[Tuple[str, float]] = []
    for item in items:
        raw = item.get(score_key, item.get("score"))
        try:
            score = float(raw or 0)
        except (TypeError, ValueError):
            continue
        if score >= min_score:
            code = str(item.get("stock_code") or "")
            if code:
                picks.append((code, score))
    picks.sort(key=lambda x: (-float(x[1]), str(x[0])))
    return picks


def score_and_rank_watching(
    entries: List[dict],
    *,
    min_score: float,
    config: Optional[dict] = None,
    neutralize: Optional[bool] = None,
    rank_mode: str = "predicted_score",
    return_model: Optional[Any] = None,
    return_models_by_code: Optional[Dict[str, Any]] = None,
    min_predicted_score: Optional[float] = None,
    min_return_score: Optional[float] = None,  # 旧名
    min_predicted_return: Optional[float] = None,  # 旧名
    allow_heuristic_baseline: bool = False,
    apply_tau_buy_gate: bool = True,
    exclude_oos_failed: bool = True,
) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
    """
    对一批已算出的 signal_item 形条目做截面中性化（可选）并排序。

    默认仅 predicted_score（ŷ）。研究 OOS 可设 allow_heuristic_baseline=True，
    用 heuristic_score（人工线性加权 0–100）作对照基线臂。
    ``apply_tau_buy_gate=False``：历史/研究路径跳过 ŷ_τ 硬闸与分钟 PIT 挂载，
    并按 ŷ_EOD 排序（不拿日线近似 ŷ_trade/blend 当选股键）。
    ``exclude_oos_failed=True``（默认）：OOS 失败组成员不进 Top（含全局 ŷ / 规则分回退）。
    """
    from core.signal.return_score import (
        apply_predicted_scores,
        apply_predicted_scores_by_model,
        clamp_rank_mode,
        rank_by_predicted_score,
        resolve_research_rank_mode,
    )

    if min_predicted_score is None:
        min_predicted_score = min_return_score
    if min_predicted_score is None:
        min_predicted_score = min_predicted_return

    cfg = config or load_signal_config()
    cs_cfg = cfg.get("cross_section") or {}
    use_neutral = cs_cfg.get("neutralize", True) if neutralize is None else bool(neutralize)
    try:
        from core.signal.neutralize import neutralize_options_from_config

        _nopt = neutralize_options_from_config(cfg)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cross_section_batch.py", exc_info=True)
        _nopt = {
            "industry_residual": bool(cs_cfg.get("industry_residual", True)),
            "size_residual": bool(cs_cfg.get("size_residual", True)),
            "size_buckets": int(cs_cfg.get("size_buckets") or 3),
            "method": str(cs_cfg.get("method") or "zscore"),
            "min_samples": int(cs_cfg.get("min_samples") or 3),
            "zscore_scale": float(cs_cfg.get("zscore_scale") or 10.0),
            "yhat_residual": bool(cs_cfg.get("yhat_residual", False)),
        }
    mode = (
        resolve_research_rank_mode(rank_mode)
        if allow_heuristic_baseline
        else clamp_rank_mode(rank_mode)
    )

    meta: Dict[str, Any] = {"applied": False, "rank_mode": mode}
    items = list(entries)
    oos_blocked: set = set()
    if exclude_oos_failed:
        try:
            from core.signal.cluster.oos_labels import codes_in_oos_failed_clusters

            oos_blocked = codes_in_oos_failed_clusters()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cross_section_batch.py", exc_info=True)
            oos_blocked = set()
    meta["oos_failed_blocked_codes"] = len(oos_blocked)

    # —— 研究对照基线：人工线性加权 ——
    if mode == "heuristic_score":
        for it in items:
            if it.get("heuristic_score") is None and it.get("score") is not None:
                try:
                    it["heuristic_score"] = float(it["score"])
                except (TypeError, ValueError):
                    pass
            it["rank_mode"] = "heuristic_score"
        if use_neutral:
            wmap = dict(cfg.get("weights") or {})
            nmeta = apply_cross_section_neutralization(
                items,
                weights=wmap,
                method=str(_nopt.get("method") or "zscore"),
                min_samples=int(_nopt.get("min_samples") or 3),
                zscore_scale=float(_nopt.get("zscore_scale") or 10.0),
                industry_residual=bool(_nopt.get("industry_residual", True)),
                size_residual=bool(_nopt.get("size_residual", True)),
                size_buckets=int(_nopt.get("size_buckets") or 3),
            )
            items = list(nmeta.get("items") or items)
            # 中性化后 score 可能已重算；同步 heuristic_score
            for it in items:
                if it.get("score") is not None:
                    try:
                        it["heuristic_score"] = float(it["score"])
                    except (TypeError, ValueError):
                        pass
            meta["applied"] = bool(nmeta.get("applied"))
            meta["neutralize"] = {
                k: nmeta.get(k) for k in ("applied", "reason", "sample_count") if k in nmeta
            }
        if oos_blocked:
            before = len(items)
            items = [
                it
                for it in items
                if str(it.get("stock_code") or "").strip() not in oos_blocked
            ]
            meta["oos_failed_excluded"] = before - len(items)
        picks = rank_scored_items(
            items, min_score=float(min_score), score_key="heuristic_score"
        )
        meta["items_by_code"] = {
            str(it.get("stock_code") or "").strip(): it
            for it in items
            if it.get("stock_code")
        }
        return picks, meta

    # —— 选股真源：predicted_score ——
    by_code = return_models_by_code or {}
    if by_code:
        items = apply_predicted_scores_by_model(
            items,
            by_code,
            write_rank_score=False,
            default_model=return_model,
        )
        mapped = sum(
            1
            for it in items
            if it.get("predicted_score") is not None
            and str(it.get("stock_code") or "").strip() in by_code
        )
        meta["return_model_source"] = "cluster_group_beta"
        meta["cluster_return_models"] = len(by_code)
        meta["cluster_predicted_mapped"] = mapped
        if return_model is not None:
            meta["return_model_fallback"] = {
                "sample_count": getattr(return_model, "sample_count", None),
                "fitted_as_of": getattr(return_model, "fitted_as_of", None),
            }
    elif return_model is not None:
        items = apply_predicted_scores(items, return_model, write_rank_score=False)
        meta["return_model_source"] = "global"
        meta["return_model"] = {
            "sample_count": getattr(return_model, "sample_count", None),
            "fitted_as_of": getattr(return_model, "fitted_as_of", None),
            "horizon_days": getattr(return_model, "horizon_days", None),
            "ridge_lambda": getattr(return_model, "ridge_lambda", None),
        }

    if use_neutral:
        meta["neutralize_skipped"] = "predicted_score_uses_factor_coefs"
        meta["neutralize_note"] = (
            "启发式 sub_scores 中性化不进 ŷ 路径；"
            "可选 cross_section.yhat_residual 对排序键做行业残差（P2a）。"
        )
    meta["rank_mode"] = mode

    # 双层 ŷ：live 才挂 ŷ_τ；历史日线关 τ 闸时只排 ŷ_EOD，不拉分钟仓
    dual_cfg = None
    rem_doc = None
    try:
        from core.signal.dual_score import (
            attach_dual_score_pit,
            buy_passes_tau_gate,
            get_dual_score_cfg,
            oo_gate_score_for_item,
            rank_key_field,
            rank_key_for_item,
            resolve_predicted_score_oo,
        )

        dual_cfg = get_dual_score_cfg(cfg)
        if apply_tau_buy_gate:
            try:
                from core.research.tau_ridge import load_tau_model

                rem_doc = load_tau_model()
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in cross_section_batch.py", exc_info=True)
                rem_doc = None
            for it in items:
                if not isinstance(it, dict):
                    continue
                attach_dual_score_pit(
                    it,
                    quote=it.get("_bt_quote"),
                    bars=it.get("_bt_bars"),
                    config=cfg,
                    rem_model_doc=rem_doc,
                )
            meta["dual_score"] = {
                "fusion_mode": dual_cfg.get("fusion_mode"),
                "w_eod": dual_cfg.get("w_eod"),
                "w_tau": dual_cfg.get("w_tau"),
                "min_predicted_score_tau": dual_cfg.get("min_predicted_score_tau"),
                "rem_model": bool(rem_doc),
            }
        else:
            meta["dual_score"] = {
                "skipped": True,
                "reason": "historical_eod_rank",
            }
    except Exception as e:
        logger.exception('unexpected error in score_and_rank_watching')
        meta["dual_score"] = {"ok": False, "reason": str(e)}
        dual_cfg = None

    # P2a：可选对 ŷ 排序键做行业截面残差（默认关；不改组 β）
    if bool(_nopt.get("yhat_residual")):
        try:
            from core.signal.neutralize import residualize_rank_scores

            rmeta = residualize_rank_scores(
                items,
                score_keys=[
                    "predicted_score",
                    "predicted_score_oo",
                    "predicted_score_eod",
                    "predicted_score_blend",
                    "score",
                ],
                by="sector",
            )
            items = list(rmeta.get("items") or items)
            meta["yhat_residual"] = {
                k: rmeta.get(k)
                for k in ("applied", "by", "touched_fields", "groups", "note")
            }
        except Exception as exc:
            logger.exception('unexpected error in score_and_rank_watching')
            meta["yhat_residual"] = {"applied": False, "reason": str(exc)}

    has_preds = any(it.get("predicted_score") is not None for it in items)
    if not has_preds and return_model is None and not by_code:
        meta["predicted_score_fallback"] = "no_model"
        picks = []
    elif dual_cfg is not None:
        floor = None if min_predicted_score is None else float(min_predicted_score)
        ranked: List[Tuple[str, float, float]] = []
        gated = 0
        oos_excluded = 0
        for it in items:
            code = str(it.get("stock_code") or "").strip()
            if not code:
                continue
            if code in oos_blocked:
                oos_excluded += 1
                continue
            oo_raw = resolve_predicted_score_oo(it)
            oo_gate = oo_gate_score_for_item(it, config=cfg)
            if oo_gate is None:
                oo_gate = oo_raw
            if oo_gate is None:
                continue
            if floor is not None and float(oo_gate) < floor:
                continue
            oo_f = float(oo_raw if oo_raw is not None else oo_gate)
            if apply_tau_buy_gate:
                ok, _reason = buy_passes_tau_gate(it, config=cfg)
                if not ok:
                    gated += 1
                    continue
                try:
                    from core.signal.y_state import buy_passes_y_check, stamp_y_state

                    if it.get("y_check") is None:
                        stamp_y_state(it, config=cfg)
                    y_ok, _y_reason = buy_passes_y_check(it, config=cfg)
                    if not y_ok:
                        gated += 1
                        meta["dual_score_y_gated"] = int(meta.get("dual_score_y_gated") or 0) + 1
                        continue
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    logger.debug("catch except Exception: in cross_section_batch.py", exc_info=True)
                    pass
                sort_key = rank_key_for_item(it, config=cfg)
                if sort_key is None:
                    continue
                rk_name = rank_key_field(config=cfg)
            else:
                # 历史日线：ŷ_τ/blend 不可靠，排序只用 ŷ_oo
                sort_key = oo_f
                rk_name = "predicted_score_oo"
            it["rank_key"] = rk_name
            # picks 第二元始终是 ŷ_oo；第三元为实际排序键
            ranked.append((code, oo_f, float(sort_key)))
        ranked.sort(key=lambda x: (-float(x[2]), str(x[0])))
        picks = [(c, oo) for c, oo, _b in ranked]
        meta["dual_score_tau_gated"] = gated
        meta["oos_failed_excluded"] = oos_excluded
        meta["rank_key"] = (
            "predicted_score_oo"
            if not apply_tau_buy_gate
            else rank_key_field(config=cfg)
        )
        meta["rank_by_oo"] = not bool(apply_tau_buy_gate)
    else:
        oos_excluded = 0
        if oos_blocked:
            kept: List[dict] = []
            for it in items:
                code = str(it.get("stock_code") or "").strip()
                if code and code in oos_blocked:
                    oos_excluded += 1
                    continue
                kept.append(it)
            items = kept
        picks = rank_by_predicted_score(
            items, min_predicted_score=min_predicted_score
        )
        if not picks:
            meta["predicted_score_fallback"] = "empty_preds"
            picks = []
        meta["oos_failed_excluded"] = oos_excluded

    meta["items_by_code"] = {
        str(it.get("stock_code") or "").strip(): it
        for it in items
        if it.get("stock_code")
    }
    return picks, meta

_OVERHEAT_ITEM_KEYS = (
    "paper_hard_reject",
    "paper_reject_reason",
    "overheat",
    "overheat_scale",
    "mom_chase_risk",
    "reject_reason",
    "factors",
)


def score_bars_as_item(
    code: str,
    scored: dict,
    *,
    sector: Optional[str] = None,
    market_cap: Optional[float] = None,
) -> dict:
    item = {
        "stock_code": code,
        "score": scored.get("score"),
        # 研究 OOS 基线用；生产选股仍以 predicted_score 为准
        "heuristic_score": scored.get("score"),
        "sub_scores": scored.get("sub_scores") or {},
        "factor_contrib": scored.get("factor_contrib") or {},
        "reasons": scored.get("reasons") or [],
        "regime": scored.get("regime") or {},
        "hard_reject": scored.get("hard_reject"),
    }
    src = scored if isinstance(scored, dict) else {}
    for k in _OVERHEAT_ITEM_KEYS:
        if k in src and src.get(k) is not None:
            item[k] = src.get(k)
    if sector is not None:
        item["sector"] = sector
    if market_cap is not None:
        item["market_cap"] = market_cap
    return item


def score_window_as_item(
    code: str,
    window: List[dict],
    *,
    horizon_days: int,
    quote: dict,
    index_bars: Optional[List[dict]] = None,
    config: Optional[dict] = None,
    fundamentals: Optional[dict] = None,
    sector: Optional[str] = None,
    market_cap: Optional[float] = None,
    required_factor_keys: Optional[List[str]] = None,
) -> Optional[dict]:
    scored = score_bars(
        window,
        horizon_days=horizon_days,
        quote=quote,
        index_bars=index_bars,
        config=config,
        fundamentals=fundamentals,
        required_factor_keys=required_factor_keys,
        # 与 score_stock 一致：过热只标 tip，不掐死 ŷ。
        # 否则回测持仓一旦 mom5≥10% 就会从打分名单消失，明细预估值冻在最后一天。
        mom3_hard_reject=False,
        stock_code=str(code or "").strip() or None,
    )
    if scored.get("hard_reject"):
        return None
    mcap = market_cap
    if mcap is None and isinstance(fundamentals, dict):
        try:
            raw = fundamentals.get("market_cap")
            mcap = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            mcap = None
    sec = sector
    if sec is None:
        try:
            from core.portfolio_optimize import _sector_for, load_sector_map

            sec = _sector_for(code, load_sector_map())
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cross_section_batch.py", exc_info=True)
            sec = None
    return score_bars_as_item(code, scored, sector=sec, market_cap=mcap)
