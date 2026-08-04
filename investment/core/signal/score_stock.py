"""单票短线评分（与 SignalEngine 共用逻辑）。"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from core.signal.scorer import score_bars
from core.signal.config import load_signal_config
from core.signal.fundamentals_bridge import fetch_score_fundamentals
from core.ports.market import (
    bars_from_quote_fallback,
    fetch_daily_bars,
    query_quote,
)
from core.store import assess_quality
from core.data_service import allows_production_score, infer_adjust, DEFAULT_ADJUST_POLICY


def _call_with_timeout(func, timeout, *args, **kwargs):
    """带超时的函数调用，使用线程实现（适用于后台线程）。

    在 worker 线程中设置 socket 全局超时，确保 akshare 内部的
    requests 调用不会永久卡住。
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
    event.wait(timeout=timeout + 2)  # 给 socket 超时留余量

    if exception[0]:
        raise exception[0]
    if not event.is_set():
        raise TimeoutError(f"操作超时（{timeout}秒）")
    return result[0]


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
    horizon_days: int = 3,
    quote: Optional[dict] = None,
    skip_fundamentals: bool = False,
    bypass_quality_gate: bool = False,
    cluster_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """拉行情 + 日线 + score_bars，返回 signal_item 形状 dict。

    P1：默认质量门禁 —— thin/empty/fallback 不进生产 score（hard_reject）。
    研究可传 bypass_quality_gate=True；历史回测引擎直接调 score_bars，不受影响。

    cluster_mode: None=读 signal_config.cluster_scoring；
    off|shadow|active — 映射组 return_model；主分=ŷ（无模型则空）。
    """
    horizon_days = max(1, min(int(horizon_days or 3), 3))
    raw = str(stock_code or "").strip()

    if quote is None:
        try:
            # 设置超时，防止行情查询卡住
            quote = _call_with_timeout(query_quote, 15, raw)
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
        # 设置超时，防止日线数据获取卡住
        bars, src = _call_with_timeout(fetch_daily_bars, 10, raw, limit=40)
        if not bars:
            bars, src = _call_with_timeout(fetch_daily_bars, 10, str(code), limit=40)
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
    if not skip_fundamentals and fund_cfg.get("enabled", True) and fund_cfg.get("fetch_on_score", True):
        fundamentals = fetch_score_fundamentals(raw) or fetch_score_fundamentals(str(code))

    # 拉观察页舆情情绪（复用 15min 缓存）；失败不影响评分
    sentiment = None
    try:
        from core.sentiment import fetch_stock_headlines
        sent_data = _call_with_timeout(fetch_stock_headlines, 12, raw, limit=5)
        if sent_data and sent_data.get("ok"):
            sentiment = sent_data.get("sentiment")
    except Exception:
        pass

    # 分组 live：解析模式与组因子系数映射
    from core.signal.cluster_live import get_cluster_scoring_cfg, lookup_code_weights

    cs_cfg = get_cluster_scoring_cfg(cfg)
    mode = (cluster_mode or cs_cfg.get("mode") or "off").strip().lower()
    if mode not in ("off", "shadow", "active"):
        mode = "off"
    if not cs_cfg.get("enabled") and cluster_mode is None:
        mode = "off"

    mapped = lookup_code_weights(str(code)) or lookup_code_weights(raw)
    weight_source = "global"
    cluster_label = None
    cluster_id = None
    cluster_version = None

    # 只打一次分：拿 sub_scores / hard_reject（ŷ 输入）；不产出规则综合分
    scored = score_bars(
        bars,
        horizon_days=horizon_days,
        quote=quote,
        fundamentals=fundamentals,
        config=cfg,
        sentiment=sentiment,
    )
    if mapped and mode in ("shadow", "active"):
        cluster_label = mapped.get("cluster_label")
        cluster_id = mapped.get("cluster_id")
        cluster_version = mapped.get("version")
        if mode == "active":
            weight_source = mapped.get("weight_source") or f"cluster:{cluster_label}"
        else:
            weight_source = "global+shadow"
    elif mode == "active":
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
    group_model = None
    global_model = None
    try:
        from core.signal.return_score import ReturnScoreModel
        from core.signal.cluster_live import lookup_code_return_model
        from core.signal.return_score_store import load_return_model

        subs = scored.get("sub_scores") or {}
        ret_raw = (mapped or {}).get("return_model") if mapped else None
        if isinstance(ret_raw, dict):
            group_model = ReturnScoreModel.from_dict(ret_raw)
        if group_model is None:
            group_model = lookup_code_return_model(str(code)) or lookup_code_return_model(
                raw
            )
        global_model, _meta = load_return_model(prefer_active=True)

        if global_model is not None:
            score_global = global_model.predict(subs)
        if group_model is not None:
            score_cluster = group_model.predict(subs)

        if group_model is not None and score_cluster is not None:
            predicted_score = score_cluster
            return_model_source = "cluster_group_beta"
            active_model = group_model
        elif global_model is not None and score_global is not None:
            predicted_score = score_global
            return_model_source = "global"
            active_model = global_model
    except Exception:
        predicted_score = None
        score_global = None
        score_cluster = None
        return_model_source = None
        active_model = None
        group_model = None
        global_model = None

    # 主分：仅 ŷ；无模型则为 None
    primary_score = None
    if predicted_score is not None and not scored.get("hard_reject"):
        primary_score = predicted_score

    delta = None
    if score_cluster is not None and score_global is not None:
        delta = round(float(score_cluster) - float(score_global), 6)

    return_model_payload = None
    score_formula = ""
    score_formula_terms = None
    factor_coefficients = None
    if active_model is not None:
        try:
            from services.paper_helpers import _active_return_model_payload, _build_score_formula

            return_model_payload = _active_return_model_payload(
                group_model=group_model,
                global_model=global_model,
                return_model_source=return_model_source,
            )
            factor_coefficients = dict(return_model_payload.get("coefficients") or {})
            score_formula_terms = active_model.explain_prediction(
                scored.get("sub_scores") or {}
            )
            score_formula = _build_score_formula(
                {
                    "sub_scores": scored.get("sub_scores"),
                    "return_model": active_model,
                }
            )
        except Exception:
            return_model_payload = None
            score_formula = ""
            score_formula_terms = None
            factor_coefficients = None

    signal_item = {
        "stock_code": code,
        "stock_name": name,
        "price": quote.get("price"),
        "change": quote.get("change"),
        "score": primary_score,
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
    }

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
