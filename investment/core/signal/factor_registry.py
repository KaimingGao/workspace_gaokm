"""因子注册表与实验框架（P9.4 / P45 / V2.1 扩展）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.signal.factors.alt_sentiment import score_alt_sentiment
from core.signal.factors.amihud import score_amihud
from core.signal.factors.dividend import score_dividend
from core.signal.factors.earnings_yield import score_earnings_yield
from core.signal.factors.gap_risk import score_gap_risk
from core.signal.factors.growth import score_growth
from core.signal.factors.idio_momentum import score_idio_momentum
from core.signal.factors.liquidity import score_liquidity
from core.signal.factors.llm_sentiment import score_llm_sentiment
from core.signal.factors.ma_slope import score_ma_slope
from core.signal.factors.momentum import pct_change, score_momentum
from core.signal.factors.money_flow import score_money_flow
from core.signal.factors.quality import score_quality
from core.signal.factors.relative_strength import score_relative_strength
from core.signal.factors.reversal import score_reversal
from core.signal.factors.size import score_size
from core.signal.factors.tail_anomaly import score_tail_anomaly
from core.signal.factors.technical_pattern import score_technical_pattern
from core.signal.factors.value import score_value
from core.signal.factors.volatility import score_volatility
from core.signal.factors.volume_price import score_volume_price
from core.signal.factors.weekly_confirm import score_weekly_confirmation

FactorFn = Callable[..., Tuple[float, dict]]

_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _register(name: str, label: str, fn: FactorFn, description: str = "") -> None:
    _REGISTRY[name] = {
        "name": name,
        "label": label,
        "description": description or "",
        "compute": fn,
    }


def _compute_momentum(bars, *, quote=None, index_bars=None, **_kw):
    return score_momentum(bars)


def _compute_volume_price(bars, *, quote=None, index_bars=None, last_change=None, **_kw):
    lc = last_change
    if lc is None and quote and quote.get("change_raw") is not None:
        lc = float(quote["change_raw"])
    elif lc is None and len(bars) >= 2:
        lc = pct_change(bars, 1)
    return score_volume_price(bars, last_change=lc)


def _compute_volatility(bars, **_kw):
    return score_volatility(bars)


def _compute_relative_strength(bars, *, quote=None, index_bars=None, config=None, last_change=None, **_kw):
    rs_cfg = (config or {}).get("relative_strength") or {}
    lc = last_change
    if lc is None and quote and quote.get("change_raw") is not None:
        lc = float(quote["change_raw"])
    return score_relative_strength(
        stock_bars=bars,
        index_bars=index_bars,
        last_change=lc,
        window_days=int(rs_cfg.get("window_days") or 20),
        min_compare_days=int(rs_cfg.get("min_compare_days") or 5),
        fallback_to_last_change=bool(rs_cfg.get("fallback_to_last_change", True)),
    )


def _compute_reversal(bars, *, stock_code=None, stock_name=None, **_kw):
    return score_reversal(bars, stock_code=stock_code, stock_name=stock_name)


def _compute_liquidity(bars, **_kw):
    return score_liquidity(bars)


def _compute_value(bars, *, fundamentals=None, **_kw):
    return score_value(bars, fundamentals=fundamentals)


def _compute_quality(bars, *, fundamentals=None, **_kw):
    return score_quality(bars, fundamentals=fundamentals)


def _compute_technical_pattern(bars, **_kw):
    return score_technical_pattern(bars)


def _compute_weekly_confirmation(bars, **_kw):
    return score_weekly_confirmation(bars)


def _compute_ma_slope(bars, **_kw):
    return score_ma_slope(bars)


def _compute_alt_sentiment(bars, *, sentiment=None, **_kw):
    return score_alt_sentiment(bars, sentiment=sentiment)


def _compute_llm_sentiment(bars, *, llm_sentiment=None, **_kw):
    return score_llm_sentiment(bars, llm_sentiment=llm_sentiment)


def _compute_gap_risk(bars, **_kw):
    return score_gap_risk(bars)


def _compute_size(bars, *, fundamentals=None, **_kw):
    return score_size(bars, fundamentals=fundamentals)


def _compute_earnings_yield(bars, *, fundamentals=None, **_kw):
    return score_earnings_yield(bars, fundamentals=fundamentals)


def _compute_growth(bars, *, fundamentals=None, **_kw):
    return score_growth(bars, fundamentals=fundamentals)


def _compute_dividend(bars, *, fundamentals=None, **_kw):
    return score_dividend(bars, fundamentals=fundamentals)


def _compute_money_flow(bars, *, money_flow=None, **_kw):
    return score_money_flow(bars, money_flow=money_flow)


def _compute_amihud(bars, **_kw):
    return score_amihud(bars)


def _compute_idio_momentum(bars, *, index_bars=None, **_kw):
    return score_idio_momentum(bars, index_bars=index_bars)


def _compute_tail_anomaly(bars, *, minute_bars=None, config=None, **_kw):
    tail_cfg = (config or {}).get("tail_anomaly") or {}
    return score_tail_anomaly(
        bars,
        minute_bars=minute_bars,
        tail_minutes=int(tail_cfg.get("tail_minutes") or 30),
    )


_register(
    "momentum",
    "动量",
    _compute_momentum,
    "近 3/5 日涨跌幅加权：温和上涨加分，过热或急跌降分。衡量短线价格趋势强度。",
)
_register(
    "volume_price",
    "量价",
    _compute_volume_price,
    "量比、价量配合、OBV、换手分位与量价背离：上涨放量/下跌缩量加分，背离降分。",
)
_register(
    "volatility",
    "波动",
    _compute_volatility,
    "以 ATR 占价格比例衡量波动：适中波动更友好，过高波动降分（偏风险约束）。",
)
_register(
    "relative_strength",
    "相对强弱",
    _compute_relative_strength,
    "个股相对基准（指数）的超额收益：跑赢基准加分，跑输降分；缺指数时可回退到自身涨跌。",
)
_register(
    "reversal",
    "反转",
    _compute_reversal,
    "涨停/连板、区间位置、振幅与波动形态：捕捉极端位置后的反转与情绪过热风险（与动量信号源分离）。",
)
_register(
    "liquidity",
    "流动性",
    _compute_liquidity,
    "近端成交额活跃度：过低（难成交）或异常放量均降分，适中流动性加分。",
)
_register(
    "value",
    "估值",
    _compute_value,
    "基于 PE/PB：适中估值区间加分，极端高估/低估降分；缺基本面不进 ŷ（omit）。",
)
_register(
    "quality",
    "质量",
    _compute_quality,
    "ROE 盈利质量：更高 ROE 加分；增速见 growth 因子。缺 ROE 不进 ŷ（omit）。",
)
_register(
    "technical_pattern",
    "技术形态",
    _compute_technical_pattern,
    "均线排列、突破、K 线形态与趋势强度（含 ADX/斜率）：多头/突破形态加分。",
)
_register(
    "weekly_confirm",
    "周线确认",
    _compute_weekly_confirmation,
    "日线聚合周线后看大周期趋势、均线位置、周动量与周量能，用于确认日线信号。",
)
_register(
    "ma_slope",
    "均线斜率",
    _compute_ma_slope,
    "多周期均线斜率与发散度：趋势向上且均线发散加分，纠缠或下行降分。",
)
_register(
    "alt_sentiment",
    "舆情",
    _compute_alt_sentiment,
    "舆情/情绪快照偏置：无舆情中性 50；偏多抬分、偏空压分。",
)
_register(
    "llm_sentiment",
    "LLM舆情",
    _compute_llm_sentiment,
    "Qwen LLM 对新闻标题+正文语义打分（研究轨/prior_only）：不进生产 ŷ；与规则舆情对照。",
)
_register(
    "gap_risk",
    "跳空风险",
    _compute_gap_risk,
    "近端隔夜跳空幅度：过大跳空降分；缺数据不进 ŷ（omit）。",
)
_register(
    "size",
    "规模",
    _compute_size,
    "log(市值) 适中区间加分；过大/过小略降分。缺市值不进 ŷ（omit）。",
)
_register(
    "earnings_yield",
    "盈利收益率",
    _compute_earnings_yield,
    "EP=1/PE_TTM：适中盈利收益率加分；与 PE/PB 分段的估值因子互补。",
)
_register(
    "growth",
    "成长",
    _compute_growth,
    "盈利/营收增速：稳健成长加分；与质量（ROE）拆分以免双计。",
)
_register(
    "dividend",
    "股息",
    _compute_dividend,
    "股息率适中加分；缺股息率不进 ŷ（omit）。",
)
_register(
    "money_flow",
    "资金流",
    _compute_money_flow,
    "优先真净流入快照；否则 OHLCV MFI 代理。缺数据中性 50。",
)
_register(
    "amihud",
    "非流动性",
    _compute_amihud,
    "Amihud 冲击成本代理：高 |收益|/成交额 降分；相对活跃度类流动性因子正交。",
)
_register(
    "idio_momentum",
    "特异动量",
    _compute_idio_momentum,
    "个股收益对指数回归残差：市场中性后的短线特异动量；无指数不进 ŷ（omit）。",
)
_register(
    "tail_anomaly",
    "尾盘异动",
    _compute_tail_anomaly,
    "尾盘成交量占比与价格斜率：放量下行降分，疑似聪明资金抢跑。",
)

def list_factors(*, include_meta: bool = False) -> List[Dict[str, str]]:
    """列出注册因子；include_meta=True 时附 FM1 sourced/proxy 状态。"""
    proxy_meta = {}
    if include_meta:
        try:
            from core.signal.factor_health import PROXY_OR_UNSOURCED

            proxy_meta = dict(PROXY_OR_UNSOURCED)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in factor_registry.py", exc_info=True)
            proxy_meta = {}
    out: List[Dict[str, str]] = []
    for k, v in _REGISTRY.items():
        row: Dict[str, Any] = {
            "name": k,
            "label": v["label"],
            "description": v.get("description") or "",
        }
        if include_meta:
            pm = proxy_meta.get(k) or {}
            row["sourced"] = not bool(pm)
            row["status"] = str(pm.get("status") or "sourced")
            row["status_note"] = str(pm.get("note") or "")
        out.append(row)
    return out


def factor_label(name: str) -> str:
    """因子展示名（中文）；未注册则回退原名。"""
    key = str(name or "").strip()
    meta = _REGISTRY.get(key)
    if meta and meta.get("label"):
        return str(meta["label"])
    return key or str(name or "")


def registered_factor_names() -> Tuple[str, ...]:
    return tuple(_REGISTRY.keys())


def compute_factor(name: str, bars: List[dict], **kwargs) -> Tuple[float, dict]:
    key = (name or "").strip()
    if key not in _REGISTRY:
        raise KeyError(f"未知因子: {key}")
    score, meta = _REGISTRY[key]["compute"](bars, **kwargs)
    return float(score), meta


def compute_configured_factors(
    bars: List[dict],
    *,
    weights: Optional[Dict[str, float]] = None,
    quote: Optional[dict] = None,
    index_bars: Optional[List[dict]] = None,
    config: Optional[dict] = None,
    last_change: Optional[float] = None,
    fundamentals: Optional[dict] = None,
    sentiment: Optional[dict] = None,
    llm_sentiment: Optional[dict] = None,
    money_flow: Optional[dict] = None,
    required_keys: Optional[Sequence[str]] = None,
    skip_factors: Optional[Sequence[str]] = None,
    stock_code: Optional[str] = None,
    stock_name: Optional[str] = None,
    minute_bars: Optional[List[dict]] = None,
    macro: Optional[dict] = None,
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, Any]]:
    """按 weights 键计算子分；``required_keys`` 即使权为 0 也算（ŷ β 同构，FS0）。"""
    wmap = dict(weights or {})
    skip = {str(x).strip() for x in (skip_factors or []) if str(x).strip()}
    for key in required_keys or []:
        k = str(key or "").strip()
        if k and k not in wmap and k in _REGISTRY:
            wmap[k] = 0.0
    sub_scores: Dict[str, float] = {}
    contribs: Dict[str, float] = {}
    meta: Dict[str, Any] = {}
    code = stock_code or (quote or {}).get("stock_code")
    name = stock_name or (quote or {}).get("stock_name")

    for name_fac, weight in wmap.items():
        if name_fac not in _REGISTRY:
            continue
        if name_fac in skip:
            continue
        try:
            w = float(weight)
        except (TypeError, ValueError):
            w = 0.0
        # 可选因子：权≈0 且非 required 时可跳过
        required = set(str(x).strip() for x in (required_keys or []) if str(x).strip())
        if abs(w) < 1e-12 and name_fac not in required and name_fac in (
            "alt_sentiment",
            "llm_sentiment",
            "money_flow",
            "tail_anomaly",
        ):
            continue
        score, fac_meta = compute_factor(
            name_fac,
            bars,
            quote=quote,
            index_bars=index_bars,
            config=config,
            last_change=last_change,
            fundamentals=fundamentals,
            sentiment=sentiment,
            llm_sentiment=llm_sentiment,
            money_flow=money_flow,
            stock_code=code,
            stock_name=name,
            minute_bars=minute_bars,
            macro=macro,
        )
        meta.update(fac_meta or {})
        # 缺输入（如规模无市值）：不进 sub_scores，ŷ 跳过该项，禁止假中性 50 进 z-score
        if isinstance(fac_meta, dict) and fac_meta.get("omit_sub_score"):
            continue
        sub_scores[name_fac] = round(score, 1)
        contribs[name_fac] = round(w * score, 2)

    return sub_scores, contribs, meta


def run_factor_experiment(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    config: Optional[dict] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    pit_fundamentals: bool = True,
) -> Dict[str, Any]:
    """对每个注册因子单独算 IC（Pearson vs forward return）。

    E2：默认按 bar 日期 PIT 解析财务；缺史当日该因子用 None 基本面（价量因子不受影响）。
    """
    from core.signal.config import load_signal_config
    from core.signal.factor_corr import pearson_with_reason

    cfg = config or load_signal_config()
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    n = len(bars or [])
    fund_cache: Dict[str, Optional[dict]] = {}
    pit_hits = 0
    pit_miss = 0

    def _fund_for(decision_date: str) -> Optional[dict]:
        nonlocal pit_hits, pit_miss
        if not pit_fundamentals:
            return fundamentals
        if not stock_code:
            return None
        if decision_date in fund_cache:
            return fund_cache[decision_date]
        try:
            from core.fundamentals_pit import resolve_fundamentals_for_score

            resolved = resolve_fundamentals_for_score(
                stock_code, as_of=decision_date, live_fallback=False
            )
            metrics = resolved.get("metrics") if resolved.get("ok") else None
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in factor_registry.py", exc_info=True)
            metrics = None
        if metrics:
            pit_hits += 1
        else:
            pit_miss += 1
        fund_cache[decision_date] = metrics
        return metrics

    rows = []
    exclusion_reasons: Dict[str, str] = {}
    for fac in list_factors():
        xs: List[float] = []
        ys: List[float] = []
        for i in range(min_history - 1, n - horizon_days):
            start = max(0, i - max_window + 1)
            window = bars[start : i + 1]
            quote = {"change_raw": 0.0, "price_raw": bars[i]["close"]}
            if i >= 1 and bars[i - 1]["close"]:
                quote["change_raw"] = round(
                    (bars[i]["close"] / bars[i - 1]["close"] - 1.0) * 100.0, 4
                )
            idx_slice = index_bars[start : i + 1] if index_bars else None
            decision_date = str((bars[i] or {}).get("date") or "")[:10]
            day_fund = _fund_for(decision_date)
            try:
                score, _meta = compute_factor(
                    fac["name"],
                    window,
                    quote=quote,
                    index_bars=idx_slice,
                    config=cfg,
                    fundamentals=day_fund,
                )
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in factor_registry.py", exc_info=True)
                continue
            c0 = bars[i]["close"]
            c1 = bars[i + horizon_days]["close"]
            if not c0:
                continue
            fr = (c1 / c0 - 1.0) * 100.0
            xs.append(score)
            ys.append(fr)

        ic_raw, reason = pearson_with_reason(xs, ys)
        ic = round(ic_raw, 4) if ic_raw is not None else None
        row = {
            "factor": fac["name"],
            "label": fac["label"],
            "sample_count": len(xs),
            "ic": ic,
        }
        if reason:
            row["exclusion_reason"] = reason
            exclusion_reasons[fac["name"]] = reason
        rows.append(row)

    note = "插件化因子 IC 实验；改权重前建议先看 IC 符号与样本量。"
    if pit_fundamentals:
        note += " 基本面因子按决策日 PIT；缺史不静默用最新快照。"
    elif fundamentals:
        note += " value/quality/growth 等使用最新基本面快照（非 point-in-time），IC 仅供方向参考。"

    return {
        "success": True,
        "horizon_days": horizon_days,
        "factor_count": len(rows),
        "factors": rows,
        "exclusion_reasons": exclusion_reasons,
        "fundamentals_used": (not pit_fundamentals and fundamentals is not None)
        or (pit_fundamentals and pit_hits > 0),
        "pit_fundamentals": bool(pit_fundamentals),
        "pit_resolve_hits": pit_hits,
        "pit_resolve_miss": pit_miss,
        "stock_code": stock_code,
        "note": note,
    }
