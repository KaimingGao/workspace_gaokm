"""单票短线评分（与 SignalEngine 共用逻辑）。"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from core.signal.scorer import score_bars
from core.signal.config import load_signal_config
from core.ports.market import (
    bars_from_quote_fallback,
    fetch_daily_bars,
    query_quote,
)
from core.store import assess_quality
from core.data_service import allows_production_score, infer_adjust, DEFAULT_ADJUST_POLICY

logger = logging.getLogger(__name__)


def _call_with_timeout(func, timeout, *args, **kwargs):
    """带超时的函数调用（仅用于无 AkShare 锁的路径，如腾讯行情）。

    日线等 AkShare 路径请用 ``_fetch_bars_isolated``，避免超时后线程仍占全局锁。
    """
    import socket

    result = [None]
    exception = [None]
    event = threading.Event()

    def worker():
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            exception[0] = e
        finally:
            socket.setdefaulttimeout(old_timeout)
            event.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    event.wait(timeout=timeout + 2)

    if exception[0]:
        raise exception[0]
    if not event.is_set():
        raise TimeoutError(f"操作超时（{timeout}秒）")
    return result[0]


def _fetch_bars_isolated(stock_code: str, *, limit: int = 40, timeout: float = 10.0):
    """子进程拉取日线：超时不占用主进程 ak_lock。

    优先读本地缓存（主进程）；仅缓存不足时走 ak_worker 进程池。
    """
    raw = str(stock_code or "").strip()
    if not raw:
        return [], "empty"
    # 1) 主进程缓存快路径（无网络、无 MiniRacer）
    try:
        from core.ports.market import resolve_market_code
        from core.store import load_daily_cache

        market, code = resolve_market_code(raw)
        if market and code:
            cached = load_daily_cache(
                market, code, min_bars=min(15, limit), max_age_hours=36.0
            )
            if cached:
                bars, meta = cached
                if bars and len(bars) >= min(15, limit):
                    src = str((meta or {}).get("data_source") or "cache")
                    return list(bars)[-int(limit) :], (
                        src if src.startswith("cache") else f"cache:{src}"
                    )
    except Exception:
        pass

    # 2) 进程池远端拉取
    try:
        from skills.common.ak_worker import batch_fetch_daily_bars
        from core.ports.market import resolve_market_code

        market, code = resolve_market_code(raw)
        if not market or not code:
            return [], "empty"
        m = str(market).upper()
        got = batch_fetch_daily_bars(
            m, [code], limit=limit, timeout=float(timeout)
        )
        bars = list((got or {}).get(code) or [])
        if bars:
            return bars[-int(limit) :], f"ak_pool:{m.lower()}"
        return [], "empty"
    except Exception:
        # 进程池不可用时回退端口（仍可能占锁，但有超时线程兜底）
        try:
            return _call_with_timeout(fetch_daily_bars, timeout, raw, limit=limit)
        except Exception:
            return [], "empty"


def _gated_reject_item(
    *,
    code: str,
    name: str,
    quote: dict,
    data_source: str,
    quality: dict,
    gate_reason: str,
    horizon_days: int,
) -> Dict[str, Any]:
    """P1：质量门禁拦截时的 signal_item（不调用 score_bars）。"""
    reason_map = {
        "data_quality_gate:fallback": "日线降级(quote_fallback)，不进生产评分",
        "data_quality_gate:empty": "无可用日线，不进生产评分",
        "data_quality_gate:thin": "日线质量 thin，不进生产评分",
    }
    reject_reason = reason_map.get(gate_reason, f"数据质量门禁：{gate_reason}")
    signal_item = {
        "stock_code": code,
        "stock_name": name,
        "price": quote.get("price"),
        "change": quote.get("change"),
        "score": None,
        "hard_reject": True,
        "reject_reason": reject_reason,
        "factors": {},
        "reasons": [reject_reason],
        "invalidation": None,
        "sub_scores": {},
        "factor_contrib": {},
        "regime": None,
        "data_source": data_source,
        "data_quality": quality,
        "adjust": infer_adjust(data_source),
        "adjust_policy": DEFAULT_ADJUST_POLICY,
        "quality_gate": True,
        "gate_reason": gate_reason,
        "horizon_days": horizon_days,
    }
    return {
        "success": True,
        "stock_code": code,
        "stock_name": name,
        "quote": quote,
        "signal_item": signal_item,
        "scored": {
            "score": None,
            "hard_reject": True,
            "reject_reason": reject_reason,
            "quality_gate": True,
        },
        "data_source": data_source,
        "data_quality": quality,
        "quality_gate": True,
    }


def score_stock(
    stock_code: str,
    *,
    horizon_days: Optional[int] = None,
    quote: Optional[dict] = None,
    skip_fundamentals: bool = False,
    skip_sentiment: bool = False,
    bypass_quality_gate: bool = False,
    cluster_mode: Optional[str] = None,
    fetch_sector_breadth: bool = False,
    sector_gap_breadth: Optional[float] = None,
    pool_gaps: Optional[list] = None,
    sector_gap_median: Optional[float] = None,
    quote_timeout: float = 15.0,
) -> Dict[str, Any]:
    """拉行情 + 日线 + score_bars，返回 signal_item 形状 dict。

    P1：默认质量门禁 —— thin/empty/fallback 不进生产 score（hard_reject）。
    研究可传 bypass_quality_gate=True；历史回测引擎直接调 score_bars，不受影响。

    ``skip_sentiment=True``：跳过标题舆情拉取（分池刷簿必开，否则 N×超时极慢）。
    ``sector_gap_breadth``：调用方预计算的截面缺口广度（刷簿一次批量后共享）。
    ``pool_gaps``：同池缺口列表，供 theme_day 与 rem 训练同构（中位 |gap|）。
    ``sector_gap_median``：同行/截面参照缺口；有则 ``gap_vs_sector = gap − median``。
    ``fetch_sector_breadth=True``：主题缺口时再拉同伴行情算广度（默认关；
    有预计算值时不再拉；刷簿并行下勿开，否则 N×批量行情卡死）。
    ``quote_timeout``：单票行情秒数（分池刷簿宜 ≤6，避免对照长时间挂起）。

    cluster_mode: None=读 signal_config.cluster_scoring；
    off — 不用组 β；shadow — 可算 score_cluster 对照，主分仍全局/空；
    active — 主分优先组 return_model ŷ（无模型则全局）。
    """
    if horizon_days is None:
        from core.signal.config import get_scoring_horizon_days

        horizon_days = get_scoring_horizon_days()
    horizon_days = max(1, min(int(horizon_days or 1), 10))
    raw = str(stock_code or "").strip()
    q_timeout = max(2.0, min(float(quote_timeout or 15.0), 30.0))

    if quote is None:
        try:
            # 设置超时，防止行情查询卡住
            quote = _call_with_timeout(query_quote, q_timeout, raw)
        except TimeoutError:
            return {
                "success": False,
                "stock_code": raw,
                "error": "行情查询超时",
            }
        except Exception as e:
            return {
                "success": False,
                "stock_code": raw,
                "error": f"行情查询失败: {e}",
            }

    name = quote.get("stock_name") or raw
    code = quote.get("stock_code") or raw
    if not quote.get("success"):
        return {
            "success": False,
            "stock_code": raw,
            "error": quote.get("error", "行情失败"),
        }

    bars = []
    data_source = "quote_fallback"
    try:
        bars, src = _fetch_bars_isolated(raw, limit=40, timeout=10.0)
        if not bars:
            bars, src = _fetch_bars_isolated(str(code), limit=40, timeout=10.0)
        if bars:
            data_source = src
    except TimeoutError:
        bars = []
    except Exception:
        bars = []

    if not bars:
        bars = bars_from_quote_fallback(quote)
        data_source = "quote_fallback"

    quality = assess_quality(bars or [], data_source=data_source)
    fallback = data_source in ("empty", "quote_fallback") or "fallback" in str(data_source)
    prod_ok, gate_reason = allows_production_score(
        quality_level=(quality or {}).get("level"),
        fallback=fallback,
    )
    if not bypass_quality_gate and not prod_ok:
        return _gated_reject_item(
            code=str(code),
            name=str(name),
            quote=quote,
            data_source=data_source,
            quality=quality,
            gate_reason=gate_reason,
            horizon_days=horizon_days,
        )

    cfg = load_signal_config()
    fund_cfg = cfg.get("fundamentals") or {}
    fundamentals = None
    fundamentals_pit_meta: Dict[str, Any] = {}
    index_meta: Dict[str, Any] = {}
    fund_depth: Dict[str, Any] = {}
    index_bars = None
    # X0：live 财务 PIT（与 panel/OLS 同源 resolve）
    if not skip_fundamentals and fund_cfg.get("enabled", True) and fund_cfg.get(
        "fetch_on_score", True
    ):
        try:
            from core.signal.live_features import resolve_live_fundamentals

            live_fund = resolve_live_fundamentals(raw, bars=bars, config=cfg)
            fundamentals = (live_fund or {}).get("metrics")
            fundamentals_pit_meta = dict((live_fund or {}).get("fundamentals_pit") or {})
            if not fundamentals:
                live_fund2 = resolve_live_fundamentals(
                    str(code), bars=bars, config=cfg
                )
                fundamentals = (live_fund2 or {}).get("metrics")
                if live_fund2 and live_fund2.get("fundamentals_pit"):
                    fundamentals_pit_meta = dict(live_fund2["fundamentals_pit"])
        except Exception as e:
            fundamentals = None
            fundamentals_pit_meta = {"ok": False, "error": str(e), "mode": "error"}

    # 刷簿 / 纸面常 skip_fundamentals：本地补数（不联网）。
    # 1) valuation_em → 市值/PE/PB；2) fundamentals 快照 → ROE/增速。
    # 否则财务因子假中性 50 → z 爆炸。
    try:
        from core.valuation_em import merge_cached_valuation

        fundamentals = merge_cached_valuation(raw, fundamentals) or fundamentals
        if fundamentals is None:
            fundamentals = merge_cached_valuation(str(code), None)
    except Exception:
        pass
    try:
        from core.fundamentals_pit import merge_local_fundamentals_snapshot

        fundamentals = merge_local_fundamentals_snapshot(raw, fundamentals) or fundamentals
        if fundamentals is None:
            fundamentals = merge_local_fundamentals_snapshot(str(code), None)
    except Exception:
        pass

    # X1：指数日线；X5：财务深度边界
    try:
        from core.signal.live_features import (
            fetch_live_index_bars,
            infer_fundamentals_depth,
        )

        try:
            from core.ports.market import resolve_market_code

            mkt = str(
                resolve_market_code(str(code)) or resolve_market_code(raw) or "CN"
            )
        except Exception:
            mkt = "CN"
        idx_pack = fetch_live_index_bars(market=mkt, limit=75)
        index_meta = {
            "ok": bool(idx_pack.get("ok")),
            "benchmark": idx_pack.get("benchmark"),
            "label": idx_pack.get("label"),
            "cached": bool(idx_pack.get("cached")),
            "reason": idx_pack.get("reason"),
            "bar_count": len(idx_pack.get("bars") or []),
        }
        if idx_pack.get("bars"):
            index_bars = idx_pack["bars"]
        fund_depth = infer_fundamentals_depth(str(code), quote=quote)
    except Exception as e:
        index_meta = {"ok": False, "reason": f"index_setup_failed:{e}"}
        fund_depth = {"fundamentals_depth": "unknown", "note": str(e)}

    # 拉观察页舆情（缓存）；默认不进 score/ŷ；先验旁路见 sentiment_prior
    sentiment_ui = None
    formula_warnings: list = []
    risk_hints: list = []
    sentiment_prior: dict = {}
    sent_cfg = cfg.get("sentiment") or {}
    include_sentiment_in_score = bool(sent_cfg.get("include_in_score", False))
    if skip_sentiment:
        formula_warnings.append("sentiment_skipped_for_speed")
        sentiment_prior = {
            "success": True,
            "role": "prior",
            "mode": "off",
            "skipped": True,
            "note": "刷簿/批打分跳过舆情拉取",
        }
    else:
        try:
            from core.sentiment import fetch_stock_headlines

            sent_data = _call_with_timeout(fetch_stock_headlines, 12, raw, limit=5)
            if sent_data and sent_data.get("ok"):
                sentiment_ui = sent_data.get("sentiment")
            elif sent_data and not sent_data.get("ok"):
                formula_warnings.append(
                    f"sentiment_unavailable:{sent_data.get('error') or 'not_ok'}"
                )
            else:
                formula_warnings.append("sentiment_empty")
        except Exception as e:
            formula_warnings.append(f"sentiment_fetch_failed:{e}")

        try:
            from core.sentiment_prior import build_sentiment_prior

            sentiment_prior = build_sentiment_prior(
                sentiment_ui if isinstance(sentiment_ui, dict) else None,
                config=cfg,
                stock_code=str(code),
            )
            risk_hints.extend(list(sentiment_prior.get("risk_hints") or []))
            for w in sentiment_prior.get("warnings") or []:
                if w and w not in formula_warnings:
                    formula_warnings.append(w)
        except Exception as e:
            formula_warnings.append(f"sentiment_prior_failed:{e}")
            sentiment_prior = {"success": False, "role": "prior", "error": str(e)}

    # FS1 / X0 / X1：财务与指数覆盖可见
    if fundamentals_pit_meta.get("error"):
        formula_warnings.append(
            f"fundamentals_pit_failed:{fundamentals_pit_meta.get('error')}"
        )
    if fundamentals_pit_meta.get("mode") == "as_of_missing":
        formula_warnings.append("fundamentals_as_of_missing")
    if fundamentals_pit_meta.get("ann_missing"):
        formula_warnings.append("fundamentals_ann_missing")
    if fundamentals_pit_meta.get("non_pit"):
        formula_warnings.append("fundamentals_non_pit_snapshot")
    if not skip_fundamentals and fund_cfg.get("enabled", True):
        if not isinstance(fundamentals, dict) or not fundamentals:
            formula_warnings.append("fundamentals_missing")
        else:
            fund_keys = ("pe", "pb", "roe", "market_cap", "dividend_yield")
            if not any(fundamentals.get(k) is not None for k in fund_keys):
                formula_warnings.append("fundamentals_empty_metrics")
    if index_meta and not index_meta.get("ok"):
        formula_warnings.append(
            str(index_meta.get("reason") or "no_index")
        )
    if fund_depth.get("fundamentals_depth") in ("hk_shallow", "us_shallow"):
        formula_warnings.append(f"fundamentals_depth:{fund_depth.get('fundamentals_depth')}")

    # 分组 live：解析模式；组 β 仅 active 进主分（FH0）
    from core.signal.cluster_live import (
        cluster_yhat_primary_allowed,
        cluster_yhat_shadow_compute_allowed,
        get_cluster_scoring_cfg,
        lookup_code_weights,
        normalize_cluster_scoring_mode,
    )

    cs_cfg = get_cluster_scoring_cfg(cfg)
    if cluster_mode is not None:
        mode = normalize_cluster_scoring_mode(cluster_mode, enabled=True)
    else:
        mode = normalize_cluster_scoring_mode(
            cs_cfg.get("mode"), enabled=bool(cs_cfg.get("enabled"))
        )

    mapped = None
    if cluster_yhat_shadow_compute_allowed(mode):
        mapped = lookup_code_weights(str(code)) or lookup_code_weights(raw)
    weight_source = "global"
    cluster_label = None
    cluster_id = None
    cluster_version = None

    # 先解析 return_model 键，保证 ŷ β 所需 sub_scores 齐套（FS0）
    required_factor_keys: list = []
    group_model_pre = None
    global_model_pre = None
    try:
        from core.signal.return_score import ReturnScoreModel
        from core.signal.cluster_live import lookup_code_return_model
        from core.signal.return_score_store import load_return_model

        if cluster_yhat_shadow_compute_allowed(mode):
            ret_raw = (mapped or {}).get("return_model") if mapped else None
            if isinstance(ret_raw, dict):
                group_model_pre = ReturnScoreModel.from_dict(ret_raw)
            if group_model_pre is None:
                group_model_pre = lookup_code_return_model(
                    str(code)
                ) or lookup_code_return_model(raw)
        global_model_pre, _meta = load_return_model(prefer_active=True)
        for m in (group_model_pre, global_model_pre):
            if m is None:
                continue
            for k in (m.coefficients or {}).keys():
                kk = str(k).strip()
                if kk and kk not in required_factor_keys:
                    required_factor_keys.append(kk)
    except Exception:
        required_factor_keys = []
        group_model_pre = None
        global_model_pre = None

    sentiment_for_score = sentiment_ui if include_sentiment_in_score else None
    scored = score_bars(
        bars,
        horizon_days=horizon_days,
        quote=quote,
        fundamentals=fundamentals,
        index_bars=index_bars,
        config=cfg,
        sentiment=sentiment_for_score,
        required_factor_keys=required_factor_keys or None,
    )
    if mapped and cluster_yhat_shadow_compute_allowed(mode):
        cluster_label = mapped.get("cluster_label")
        cluster_id = mapped.get("cluster_id")
        cluster_version = mapped.get("version")
        if cluster_yhat_primary_allowed(mode):
            weight_source = mapped.get("weight_source") or f"cluster:{cluster_label}"
        else:
            weight_source = "global+shadow"
    elif cluster_yhat_primary_allowed(mode):
        weight_source = "global_fallback"

    try:
        from core.portfolio_optimize import _sector_for, load_sector_map

        sector = _sector_for(str(code), load_sector_map())
    except Exception:
        sector = "其他"

    market_cap = None
    if isinstance(fundamentals, dict) and fundamentals.get("market_cap") is not None:
        try:
            market_cap = float(fundamentals["market_cap"])
        except (TypeError, ValueError):
            market_cap = None

    predicted_score = None
    score_global = None
    score_cluster = None
    return_model_source = None
    active_model = None
    group_model = group_model_pre
    global_model = global_model_pre
    try:
        subs = dict(scored.get("sub_scores") or {})
        if not include_sentiment_in_score:
            subs.pop("alt_sentiment", None)

        if global_model is not None:
            score_global = global_model.predict(subs)
        if group_model is not None:
            score_cluster = group_model.predict(subs)

        if (
            cluster_yhat_primary_allowed(mode)
            and group_model is not None
            and score_cluster is not None
        ):
            predicted_score = score_cluster
            return_model_source = "cluster_group_beta"
            active_model = group_model
        elif global_model is not None and score_global is not None:
            predicted_score = score_global
            return_model_source = "global"
            active_model = global_model
        elif (
            # shadow 且无全局 return_model 产物时：用组 ŷ 顶住主分，避免全表「—」
            mode == "shadow"
            and group_model is not None
            and score_cluster is not None
        ):
            predicted_score = score_cluster
            return_model_source = "cluster_shadow_fallback"
            active_model = group_model
            formula_warnings.append(
                "no_global_return_model:shadow_uses_cluster_yhat"
            )
    except Exception:
        predicted_score = None
        score_global = None
        score_cluster = None
        return_model_source = None
        active_model = None
        group_model = None
        global_model = None

    # 主分：仅 ŷ；无模型则为 None（启发式分见 heuristic_score）
    primary_score = None
    if predicted_score is not None and not scored.get("hard_reject"):
        primary_score = predicted_score

    heuristic_score = None
    try:
        if scored.get("score") is not None and scored.get("score") != "":
            heuristic_score = float(scored.get("score"))
    except (TypeError, ValueError):
        heuristic_score = None

    delta = None
    if score_cluster is not None and score_global is not None:
        delta = round(float(score_cluster) - float(score_global), 6)

    return_model_payload = None
    score_formula = ""
    score_formula_terms = None
    factor_coefficients = None
    if active_model is not None:
        try:
            from core.signal.score_view import (
                active_return_model_payload,
                build_score_formula,
            )

            return_model_payload = active_return_model_payload(
                group_model=group_model,
                global_model=global_model,
                return_model_source=return_model_source,
            )
            factor_coefficients = dict(return_model_payload.get("coefficients") or {})
            score_formula_terms = active_model.explain_prediction(
                {
                    k: v
                    for k, v in (scored.get("sub_scores") or {}).items()
                    if include_sentiment_in_score or k != "alt_sentiment"
                }
            )
            # FS3：公式始终展示 alt_sentiment 项（闸关或 β=0 时贡献记 0）
            try:
                coef_alt = factor_coefficients.get("alt_sentiment")
                alt_b = float(coef_alt) if coef_alt is not None else 0.0
            except (TypeError, ValueError):
                alt_b = 0.0
            if score_formula_terms is None:
                score_formula_terms = {
                    "intercept": float(getattr(active_model, "intercept", 0.0) or 0.0),
                    "total": float(predicted_score or 0.0),
                    "terms": [],
                }
            terms = list(score_formula_terms.get("terms") or [])
            if not any(str(t.get("key")) == "alt_sentiment" for t in terms):
                from core.signal.factor_registry import factor_label

                terms.append(
                    {
                        "key": "alt_sentiment",
                        "label": factor_label("alt_sentiment"),
                        "beta": round(alt_b, 6),
                        "z": 0.0,
                        "contrib": 0.0,
                        "gated": not include_sentiment_in_score,
                        "note": (
                            "闸关·不进 ŷ"
                            if not include_sentiment_in_score
                            else ("β≈0" if abs(alt_b) < 1e-12 else "")
                        ),
                    }
                )
            # 规模缺市值：不进 sub_scores，公式里仍展示一项（贡献 0）便于对账
            scored_factors = scored.get("factors") or {}
            if (
                "size" in factor_coefficients
                and "size" not in (scored.get("sub_scores") or {})
                and not any(str(t.get("key")) == "size" for t in terms)
            ):
                from core.signal.factor_registry import factor_label

                try:
                    size_b = float(factor_coefficients.get("size") or 0.0)
                except (TypeError, ValueError):
                    size_b = 0.0
                note = "缺市值·不进 ŷ"
                if scored_factors.get("size_missing"):
                    note = "缺市值·不进 ŷ"
                terms.append(
                    {
                        "key": "size",
                        "label": factor_label("size"),
                        "beta": round(size_b, 6),
                        "z": 0.0,
                        "contrib": 0.0,
                        "gated": True,
                        "note": note,
                    }
                )
            score_formula_terms = dict(score_formula_terms)
            score_formula_terms["terms"] = terms
            score_formula = build_score_formula(
                {
                    "sub_scores": {
                        k: v
                        for k, v in (scored.get("sub_scores") or {}).items()
                        if include_sentiment_in_score or k != "alt_sentiment"
                    },
                    "return_model": active_model,
                }
            )
        except Exception as e:
            return_model_payload = None
            score_formula = ""
            score_formula_terms = None
            factor_coefficients = None
            formula_warnings.append(f"score_formula_failed:{e}")

    alt_beta = None
    if isinstance(factor_coefficients, dict) and "alt_sentiment" in factor_coefficients:
        try:
            alt_beta = float(factor_coefficients["alt_sentiment"])
        except (TypeError, ValueError):
            alt_beta = None

    signal_item = {
        "stock_code": code,
        "stock_name": name,
        "price": quote.get("price"),
        "change": quote.get("change"),
        "score": primary_score,
        "heuristic_score": heuristic_score,
        "predicted_score": predicted_score,
        "return_model_source": return_model_source,
        "rank_mode": "predicted_score",
        "hard_reject": scored.get("hard_reject"),
        "reject_reason": scored.get("reject_reason"),
        "factors": scored.get("factors"),
        "reasons": scored.get("reasons"),
        "invalidation": scored.get("invalidation"),
        "sub_scores": scored.get("sub_scores"),
        "factor_contrib": scored.get("factor_contrib"),
        "regime": scored.get("regime"),
        "sector": sector,
        "market_cap": market_cap,
        "data_source": data_source,
        "data_quality": quality,
        "adjust": infer_adjust(data_source),
        "adjust_policy": DEFAULT_ADJUST_POLICY,
        "quality_gate": False,
        "horizon_days": horizon_days,
        "cluster_mode": mode,
        "weight_source": weight_source,
        "cluster_label": cluster_label,
        "cluster_id": cluster_id,
        "cluster_version": cluster_version,
        "score_global": score_global,
        "score_cluster": score_cluster,
        "delta_vs_global": delta,
        "return_model": return_model_payload,
        "factor_coefficients": factor_coefficients,
        "score_formula": score_formula or None,
        "score_formula_terms": score_formula_terms,
        "warnings": list(formula_warnings),
        "risk_hints": list(risk_hints),
        "sentiment_prior": sentiment_prior,
        "sentiment_include_in_score": include_sentiment_in_score,
        "watching_sentiment": sentiment_ui,
        "alt_sentiment_beta": alt_beta,
        "alt_sentiment_in_yhat": bool(
            include_sentiment_in_score
            and alt_beta is not None
            and abs(alt_beta) > 1e-12
            and (scored.get("sub_scores") or {}).get("alt_sentiment") is not None
        ),
        "fundamentals_pit": fundamentals_pit_meta or None,
        "index_meta": index_meta or None,
        "fundamentals_depth": fund_depth.get("fundamentals_depth"),
        "fundamentals_depth_meta": fund_depth or None,
        "feature_isomorphism_track": "X0-X5",
    }

    # R3 / A1：双层 ŷ_τ（不替换 predicted_score）
    try:
        from core.event_prior import (
            build_event_prior_from_quote,
            compute_sector_gap_breadth_live,
            gap_pct_from_quote_bars,
            get_event_prior_cfg,
        )
        from core.signal.dual_score import apply_tau_score_fields
        from quant.research.rem_ridge import predict_rem_from_features

        gap_v = gap_pct_from_quote_bars(quote, bars)
        ep_cfg = get_event_prior_cfg()
        trigger = float(ep_cfg.get("gap_trigger_pct") or 2)
        sector_breadth = None
        if sector_gap_breadth is not None:
            try:
                sector_breadth = float(sector_gap_breadth)
            except (TypeError, ValueError):
                sector_breadth = None
        # 默认不拉同伴行情：刷簿 8 路并行 × 每票 40 行情会把对照卡死数分钟。
        # 刷簿路径应预计算 sector_gap_breadth；纸面调仓另有批量路径。
        elif (
            fetch_sector_breadth
            and gap_v is not None
            and float(gap_v) >= trigger
        ):
            try:
                from core.watching_store import read_watching

                watch_codes = [
                    str(c).strip()
                    for c in ((read_watching() or {}).get("watchlist") or [])
                    if str(c).strip()
                ][:40]
                if watch_codes:
                    br = compute_sector_gap_breadth_live(
                        watch_codes,
                        gap_trigger_pct=trigger,
                        focus_code=str(code),
                        quotes={str(code): quote} if quote else None,
                    )
                    sector_breadth = br.get("breadth")
            except Exception:
                logger.warning("sector gap breadth failed for %s", code, exc_info=True)
        feats = {
            "gap_pct": gap_v,
            "sector_gap_breadth": sector_breadth,
            "theme_day": 0.0,
        }
        try:
            from core.research.rem_panel import (
                gap_atr_from_hist,
                gap_vs_sector_value,
                hist_bars_pit,
                _finite_median,
            )

            asof = ""
            if quote:
                asof = str(quote.get("date") or quote.get("trade_date") or "")[:10]
            hist = hist_bars_pit(bars, asof_date=asof)
            feats["gap_atr"] = gap_atr_from_hist(gap_v, hist)
            ref = sector_gap_median
            if ref is None and isinstance(pool_gaps, (list, tuple)) and pool_gaps:
                try:
                    ref = _finite_median(
                        [float(g) for g in pool_gaps if g is not None]
                    )
                except (TypeError, ValueError):
                    ref = None
            feats["gap_vs_sector"] = gap_vs_sector_value(gap_v, ref)
        except Exception:
            logger.debug("tau Z extras skipped for %s", code, exc_info=True)
        try:
            from core.research.rem_theme import resolve_theme_day

            feats["theme_day"] = resolve_theme_day(
                gap_pct=gap_v,
                sector_breadth=sector_breadth,
                pool_gaps=pool_gaps if isinstance(pool_gaps, (list, tuple)) else None,
                gap_trigger_pct=trigger,
            )
        except Exception:
            feats["theme_day"] = (
                1.0
                if (gap_v is not None and abs(float(gap_v)) >= trigger)
                else 0.0
            )
        # rem 头只吃 Z；日线 sub_scores 已在 ŷ_EOD，勿再塞进 feats

        # 可选：仅读本地分钟缓存附加 ret_open_to_tau（不拉网）
        as_of_tau_override = None
        y_spec_override = None
        try:
            from core.signal.dual_score import get_dual_score_cfg

            ds_cfg = get_dual_score_cfg()
            if ds_cfg.get("enable_minute_tau"):
                hm = str(ds_cfg.get("minute_tau_hm") or "09:45")
                trade_day = ""
                if bars:
                    trade_day = str((bars[-1] or {}).get("date") or "")[:10]
                if not trade_day and quote:
                    trade_day = str(quote.get("date") or quote.get("trade_date") or "")[
                        :10
                    ]
                open_px = None
                if quote:
                    try:
                        open_px = float(
                            quote.get("open")
                            or quote.get("open_price")
                            or 0.0
                        ) or None
                    except (TypeError, ValueError):
                        open_px = None
                if open_px is None and bars:
                    try:
                        open_px = float((bars[-1] or {}).get("open") or 0.0) or None
                    except (TypeError, ValueError):
                        open_px = None
                if trade_day and open_px and open_px > 0:
                    from core.research.rem_panel import price_at_tau_from_minutes
                    from core.store import load_minute_cache
                    from skills.common.history import resolve_market_code

                    mkt, pure = resolve_market_code(str(code))
                    packed = load_minute_cache(
                        mkt or "CN",
                        pure or str(code),
                        period="5",
                        min_bars=1,
                        max_age_hours=36.0,
                    )
                    if packed:
                        minute_bars, _meta = packed
                        px_tau = price_at_tau_from_minutes(
                            minute_bars, trade_date=trade_day, tau_hm=hm
                        )
                        if px_tau is not None and px_tau > 0:
                            feats["ret_open_to_tau"] = round(
                                (float(px_tau) / float(open_px) - 1.0) * 100.0, 6
                            )
                            as_of_tau_override = f"{trade_day}T{hm}:00+08:00"
                            y_spec_override = {
                                "tau": hm,
                                "formula": f"close[T]/price[{hm}]-1",
                                "note": "分钟缓存命中；无网拉；模型缺特征时 z≈0",
                            }
        except Exception:
            logger.debug("minute tau attach skipped for %s", code, exc_info=True)

        rem_yhat = predict_rem_from_features(feats)
        apply_tau_score_fields(
            signal_item,
            rem_yhat=rem_yhat,
            gap_pct=gap_v,
            feats=feats,
            as_of_tau=as_of_tau_override,
            y_spec_override=y_spec_override,
        )
        trade = signal_item.get("predicted_score_tau")
        ep = build_event_prior_from_quote(
            quote,
            bars,
            rem_yhat=trade if trade is not None else rem_yhat,
            sector_breadth=sector_breadth,
            stock_code=str(code),
        )
        signal_item["event_prior"] = ep
    except Exception:
        logger.warning("rem/event_prior attach failed for %s", code, exc_info=True)

    return {
        "success": True,
        "stock_code": code,
        "stock_name": name,
        "quote": quote,
        "signal_item": signal_item,
        "scored": scored,
        "data_source": data_source,
        "data_quality": quality,
        "quality_gate": False,
        "cluster_mode": mode,
    }
