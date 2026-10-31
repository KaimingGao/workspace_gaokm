"""日线因子特征：算 sub_scores / 门禁；选股真源为 ŷ，不再产出规则综合分。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional

from core.signal.config import get_rank_defaults, load_signal_config
from core.signal.factors.meta.registry import compute_configured_factors
from core.signal.factors.momentum import pct_change
from core.signal.factors.pre_trade import pre_trade_check
from core.signal.regime import assess_regime


def score_bars(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    quote: Optional[dict] = None,
    index_bars: Optional[List[dict]] = None,
    config: Optional[dict] = None,
    fundamentals: Optional[dict] = None,
    sentiment: Optional[dict] = None,
    required_factor_keys: Optional[List[str]] = None,
    skip_factors: Optional[List[str]] = None,
    mom3_hard_reject: bool = True,
    stock_code: Optional[str] = None,
    stock_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    对单票日线算因子特征与硬门禁。

    产出 ``sub_scores``（ŷ 输入）、``hard_reject``、``regime`` 元数据。
    ``score`` 恒为 None（规则加权总分已清理）。

    ``required_factor_keys``：即使 weights 权为 0 也计算（ŷ β 同构）。
    ``skip_factors``：强制跳过（如舆情闸关时跳过 alt_sentiment）。
    """
    cfg = config or load_signal_config()
    weights = cfg.get("weights") or {}

    hr = cfg.get("hard_reject") or {}
    min_bars = int(hr.get("min_bars", 2))
    gain_max = float(hr.get("mom3_gain_max_pct", 15))
    loss_min = float(hr.get("mom3_loss_min_pct", -12))
    gain5_max = float(hr.get("mom5_gain_max_pct", 10))
    day_gain_max = float(hr.get("day_gain_max_pct", 6))
    stop_pct = float((cfg.get("invalidation") or {}).get("stop_pct", 0.03))

    horizon_days = max(1, min(int(horizon_days or 3), 10))

    if not bars or len(bars) < min_bars:
        return {
            "score": None,
            "hard_reject": True,
            "reject_reason": "日线数据不足",
            "factors": {},
            "reasons": [],
            "invalidation": [],
            "sub_scores": {},
            "factor_contrib": {},
            "regime": {},
        }

    mom3 = pct_change(bars, min(3, len(bars) - 1))
    mom5 = pct_change(bars, min(5, len(bars) - 1)) if len(bars) > 5 else mom3

    # 检查是否配置了"硬拒绝改为降权"
    soft_reject = bool((cfg.get("hard_reject") or {}).get("soft_reject", False))
    mom3_notes: List[str] = []
    apply_mom3_reject = bool(mom3_hard_reject) and not soft_reject
    hard_reject = False
    reject_reason = ""

    if mom3 is not None and mom3 >= gain_max:
        if apply_mom3_reject:
            hard_reject = True
            reject_reason = f"近3日涨幅过大({mom3:.1f}%)，短线追高风险高"
            mom3_notes.append(reject_reason)
        else:
            mom3_notes.append(f"近3日涨幅 {mom3:.1f}%（不硬拒，交给 ŷ / 过热标注）")

    if mom3 is not None and mom3 <= loss_min:
        if apply_mom3_reject:
            hard_reject = True
            reject_reason = f"近3日跌幅过大({mom3:.1f}%)，短线动能偏弱"
            mom3_notes.append(reject_reason)
        else:
            mom3_notes.append(f"近3日跌幅 {mom3:.1f}%（不硬拒，交给 ŷ / 过热标注）")

    if mom5 is not None and mom5 >= gain5_max:
        msg = f"近5日涨幅过大({mom5:.1f}%≥{gain5_max:g}%)"
        if apply_mom3_reject:
            hard_reject = True
            reject_reason = reject_reason or msg
            mom3_notes.append(msg)
        else:
            mom3_notes.append(f"{msg}（不硬拒，交给过热标注）")

    last_change = None
    if quote and quote.get("change_raw") is not None:
        last_change = float(quote["change_raw"])
    elif len(bars) >= 2:
        last_change = pct_change(bars, 1)

    if last_change is None and bars:
        try:
            o = float(bars[-1].get("open") or 0)
            c = float(bars[-1].get("close") or 0)
            if o > 0:
                last_change = (c / o - 1.0) * 100.0
        except (TypeError, ValueError):
            last_change = None

    if last_change is not None and last_change >= day_gain_max:
        msg = f"当日涨幅过大({last_change:.1f}%≥{day_gain_max:g}%)"
        if apply_mom3_reject:
            hard_reject = True
            reject_reason = reject_reason or msg
            mom3_notes.append(msg)
        else:
            mom3_notes.append(f"{msg}（不硬拒，交给过热标注）")

    # 前置风控检查（仅留痕；不再扣规则总分）
    risk_config = cfg.get("pre_trade_risk", {})
    if risk_config and risk_config.get("enabled", True):
        _passed, risk_checks = pre_trade_check(bars, config=risk_config)
    else:
        risk_checks = {}

    macro_ctx = None
    try:
        from core.market.context import load_market_context

        macro_ctx = load_market_context().get("macro")
    except Exception:  # noqa: BLE001
        logger.debug("macro context load skipped", exc_info=True)

    # regime 仅作元数据（仓位/UI）；不再改因子权或扣规则分
    regime_info = assess_regime(index_bars, cfg.get("regime"), macro=macro_ctx)
    factor_weights = dict(weights)

    skip = list(skip_factors or [])
    # FS0：闸关或不传 sentiment 时不计算 alt_sentiment（避免中性 50 进 ŷ）
    sent_cfg = cfg.get("sentiment") or {}
    if not bool(sent_cfg.get("include_in_score", False)) or sentiment is None:
        if "alt_sentiment" not in skip:
            skip.append("alt_sentiment")

    minute_bars = None
    code_for_minute = stock_code or (quote or {}).get("stock_code")
    w_tail = float((weights or {}).get("tail_anomaly") or 0)
    req_set = {str(x).strip() for x in (required_factor_keys or []) if str(x).strip()}
    skip_minute_io = bool((cfg.get("scoring") or {}).get("skip_minute_io"))
    if (
        code_for_minute
        and (w_tail > 0 or "tail_anomaly" in req_set)
        and not skip_minute_io
    ):
        try:
            from core.market import resolve_market_code
            from core.store import load_minute_cache

            market, bare = resolve_market_code(str(code_for_minute))
            if market == "CN" and bare:
                packed = load_minute_cache(
                    "CN",
                    bare,
                    period="5",
                    min_bars=4,
                    max_age_hours=12,
                )
                if packed:
                    minute_bars, _meta = packed
        except Exception:  # noqa: BLE001
            logger.debug("minute cache for tail_anomaly skipped", exc_info=True)

    sub_scores, factor_contrib, factors = compute_configured_factors(
        bars,
        weights=factor_weights,
        quote=quote,
        index_bars=index_bars,
        config=cfg,
        last_change=last_change,
        fundamentals=fundamentals,
        sentiment=sentiment,
        required_keys=required_factor_keys,
        skip_factors=skip,
        stock_code=stock_code or (quote or {}).get("stock_code"),
        stock_name=stock_name or (quote or {}).get("stock_name"),
        minute_bars=minute_bars,
        macro=macro_ctx,
    )

    if last_change is not None:
        factors["last_change"] = round(last_change, 2)
    factors["last_close"] = bars[-1]["close"]

    if risk_checks:
        factors["risk_check"] = risk_checks

    reasons = []
    if mom3 is not None:
        reasons.append(f"近3日涨跌 {mom3:+.2f}%")
    reasons.extend(mom3_notes)
    if factors.get("volume_ratio") is not None:
        reasons.append(f"量比约 {factors['volume_ratio']:.2f}")
    if factors.get("turnover_ratio") is not None:
        reasons.append(f"成交额比 {factors['turnover_ratio']:.2f}")
    if factors.get("reversal_rsi") is not None:
        reasons.append(f"RSI(14) {factors['reversal_rsi']:.1f}")
    if factors.get("value_pe") is not None:
        reasons.append(f"PE 约 {factors['value_pe']:.1f}")
    if factors.get("quality_roe") is not None:
        reasons.append(f"ROE 约 {factors['quality_roe']:.1f}%")
    if factors.get("excess_return_pct") is not None:
        reasons.append(f"相对基准超额 {factors['excess_return_pct']:+.2f}%")
    elif last_change is not None:
        reasons.append(f"当日涨跌 {last_change:+.2f}%")
    if factors.get("atr_pct") is not None:
        reasons.append(f"近5日波动(ATR%) {factors['atr_pct']:.2f}%")

    if regime_info.get("reason"):
        reasons.append(regime_info["reason"])

    alt_label = factors.get("alt_sentiment_label")
    if alt_label and alt_label not in ("neutral", ""):
        reasons.append(f"舆情因子 {alt_label}")

    last_close = bars[-1]["close"]
    stop_ref = round(last_close * (1 - stop_pct), 2)
    invalidation = [
        f"跌破参考位约 {stop_ref}（相对最新价约 -{stop_pct*100:.0f}%）可视为短线观察失效",
        "若放量下跌且跌破近3日低点，降低关注优先级",
    ]
    if horizon_days <= 2:
        invalidation.append("1～2 天窗口内若无法站稳均价/缺口，观望优于追涨")

    out = {
        "score": None,
        "hard_reject": hard_reject,
        "reject_reason": reject_reason,
        "mom3_chase_risk": bool(
            (mom3 is not None and (mom3 >= gain_max or mom3 <= loss_min))
            or (mom5 is not None and mom5 >= gain5_max)
            or (last_change is not None and last_change >= day_gain_max)
        ),
        "factors": factors,
        "reasons": reasons,
        "invalidation": invalidation,
        "sub_scores": sub_scores,
        "factor_contrib": dict(factor_contrib),
        "regime": regime_info,
    }
    try:
        from core.signal.overheat_gate import annotate_item_overheat

        annotate_item_overheat(out, config=cfg)
    except Exception:  # noqa: BLE001
        logger.debug("annotate_item_overheat skipped", exc_info=True)
    return out


def rank_candidates(
    scored: List[dict],
    *,
    limit: int = 8,
    min_score: float = 55.0,
    config: Optional[dict] = None,
) -> List[dict]:
    """过滤硬拒绝与低 ŷ，按 predicted_score（或 score）排序截断。"""
    cfg = config or load_signal_config()
    defaults = get_rank_defaults(cfg)
    from core.signal.score_display import resolve_buy_floor

    # 显式传入非默认值时尊重调用方；默认 55 在收益分下改为无下限
    if min_score == 55.0:
        min_score = resolve_buy_floor()
    if limit == 8:
        limit = int(defaults["default_limit"])

    limit = max(1, min(int(limit or 8), 15))
    floor = float(min_score)

    def _rank_val(s: dict) -> float:
        for k in ("predicted_score", "score"):
            v = s.get(k)
            if v is None or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return float("-inf")

    kept = [
        s
        for s in scored
        if not s.get("hard_reject") and _rank_val(s) >= floor
    ]
    kept.sort(
        key=lambda x: (
            -_rank_val(x),
            str(x.get("stock_code") or ""),
        )
    )
    return kept[:limit]
