"""ŷ_oo 训练面板：y = open[T+h]/open[T]−1；特征 = 注册子因子 + raw_* / raw_aoo_*。

决策在 T 开盘（PIT 窗不含当日收盘）。与 ``co_panel`` / ``tau_panel`` 并列；
矩阵堆叠见 ``oo_ridge_compact.assemble_oo_panels``。
"""

from __future__ import annotations

import logging
from typing import Any, Collection, Dict, List, Optional, Sequence, Tuple

from core.signal.factors.meta.registry import compute_factor, registered_factor_names

logger = logging.getLogger(__name__)


def _forward_return(bars: List[dict], idx: int, horizon: int) -> Optional[float]:
    """ŷ_oo 标签：open[idx+h]/open[idx]−1（百分点）。"""
    from core.signal.yhat_windows import forward_oo_pct

    return forward_oo_pct(bars, idx, horizon)


def _research_sub_scores(
    window: List[dict],
    *,
    quote: Optional[dict] = None,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    sentiment: Optional[dict] = None,
    factor_names: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Optional[float]]:
    """研究用：逐个算注册因子；默认绕开 score_bars / regime。

    除了 0-100 sub_score 外，同时把 meta 中的原始信号值以
    ``raw_<factor>_<meta_key>`` 形式放入 row，供 LTR / Ridge 使用原始信号。
    """
    names = factor_names or registered_factor_names()
    row: Dict[str, Optional[float]] = {}
    for key in names:
        try:
            score, fac_meta = compute_factor(
                key,
                window,
                quote=quote,
                index_bars=index_bars,
                fundamentals=fundamentals,
                sentiment=sentiment,
                money_flow=None,
            )
            row[key] = float(score)
            # 暴露原始信号值，前缀 raw_<factor>_ 避免键冲突
            # alpha_oo 开锚列用 raw_aoo_*（与 ALPHA_OO_RAW_PREFIX 一致）
            if isinstance(fac_meta, dict):
                if str(key) == "alpha_oo":
                    from core.signal.factors.alpha_oo import ALPHA_OO_RAW_PREFIX

                    raw_prefix = ALPHA_OO_RAW_PREFIX
                else:
                    raw_prefix = f"raw_{key}_"
                for mk, mv in fac_meta.items():
                    if mk in ("omit_sub_score", "ok"):
                        continue
                    # 跳过 alpha_oo_insufficient_history 等标记（bool 也是 int）
                    if str(mk).startswith(f"{key}_"):
                        continue
                    if isinstance(mv, (int, float)) and not isinstance(mv, bool):
                        row[f"{raw_prefix}{mk}"] = float(mv)
                    elif mv is not None:
                        # 非数值（如 rs_source="index"）跳过
                        pass
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            row[key] = None
    return row


def _resolve_factor_names(
    *,
    respect_regime: bool,
    index_bars: Optional[List[dict]],
    config: Optional[dict],
    decision_date: Optional[str] = None,
    respect_macro_regime: bool = False,
) -> Tuple[str, ...]:
    """X4：respect_regime=True 时与 live enabled_factors 对齐。"""
    if not respect_regime:
        return registered_factor_names()
    base = registered_factor_names()
    try:
        from core.signal.config import load_signal_config
        from core.signal.regime import assess_regime

        cfg = config or load_signal_config()
        macro = None
        if respect_macro_regime and decision_date:
            try:
                from core.research.macro_asof import load_macro_view_asof

                macro = load_macro_view_asof(decision_date)
            except Exception:
                logger.debug("macro asof for regime skipped", exc_info=True)
        info = assess_regime(index_bars, (cfg or {}).get("regime"), macro=macro or None)
        adj = (info or {}).get("adjustments") or {} if isinstance(info, dict) else {}
        enabled = adj.get("enabled_factors")
        if enabled:
            names = tuple(str(x) for x in enabled if str(x).strip())
            if names:
                return names
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        pass
    return base


def collect_oo_panel(
    bars: List[dict],
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
    index_bars: Optional[List[dict]] = None,
    fundamentals: Optional[dict] = None,
    stock_code: Optional[str] = None,
    pit_fundamentals: bool = True,
    sentiment_pit: bool = False,
    respect_regime: bool = False,
    respect_macro_regime: bool = False,
    config: Optional[dict] = None,
    excess_mode: str = "none",
    keep_dates: Optional[Collection[str]] = None,
) -> Tuple[List[Dict[str, Optional[float]]], List[float], List[str]]:
    """单票 ŷ_oo 面板：对齐子因子与 open→open 前瞻收益（对齐 ``collect_co_panel``）。

    返回 ``(xs, ys, decision_dates)``；dates 与 xs/ys 等长（YYYY-MM-DD），供日历切分 / WF。

    默认全量注册因子（不经 regime 白名单）；因子可缺测（None）。
    E2：默认按决策日 PIT 解析财务。
    FS2：``sentiment_pit=True`` 时按 as_of 注入 alt_sentiment（需标题历史）。
    X4：``respect_regime=True`` 时因子集对齐 live regime.enabled_factors。
    ``excess_mode``：none | index（P2b；index 时扣同期指数前瞻收益，缺指数则跳过该样本）。
    ``keep_dates``：给定时只为这些决策日计算并保留特征行。
    """
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_history = max(5, int(min_history or 12))
    em = str(excess_mode or "none").strip().lower()
    if em in ("index", "excess", "vs_index", "benchmark"):
        em = "index"
    else:
        em = "none"
    factor_names = _resolve_factor_names(
        respect_regime=respect_regime,
        index_bars=index_bars,
        config=config,
        respect_macro_regime=respect_macro_regime,
    )
    from core.signal.factors.alpha_oo import bump_window_for_alpha_oo

    min_history, max_window = bump_window_for_alpha_oo(
        factor_names,
        min_history=min_history,
        max_window=max_window,
    )
    fund_cache: Dict[str, Optional[dict]] = {}
    sent_cache: Dict[str, Optional[dict]] = {}

    def _fund_for(decision_date: str) -> Optional[dict]:
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
            metrics = None
        fund_cache[decision_date] = metrics
        return metrics

    def _sent_for(decision_date: str) -> Optional[dict]:
        if not sentiment_pit or not stock_code:
            return None
        if decision_date in sent_cache:
            return sent_cache[decision_date]
        try:
            from core.sentiment import sentiment_as_of

            pack = sentiment_as_of(stock_code, decision_date)
            sent = pack.get("sentiment") if pack.get("ok") else None
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            sent = None
        sent_cache[decision_date] = sent
        return sent

    xs: List[Dict[str, Optional[float]]] = []
    ys: List[float] = []
    dates: List[str] = []
    keep = None
    if keep_dates is not None:
        keep = {str(d)[:10] for d in keep_dates if str(d or "").strip()[:10]}
    n = len(bars or [])
    idx_by_d: Dict[str, int] = {}
    if index_bars:
        idx_by_d = {
            str(b.get("date") or "")[:10]: j
            for j, b in enumerate(index_bars)
            if b.get("date")
        }
    from core.signal.yhat_windows import pit_oo_window_quote

    for i in range(min_history - 1, n - horizon_days):
        decision_date = str((bars[i] or {}).get("date") or "")[:10]
        if keep is not None and decision_date not in keep:
            continue
        start = max(0, i - max_window + 1)
        window, quote = pit_oo_window_quote(bars, i, start)
        if len(window) < 2:
            continue

        idx_slice = index_bars[start:i] if index_bars else None

        fr = _forward_return(bars, i, horizon_days)
        if fr is None:
            continue
        if em == "index":
            from core.research.beta_accuracy import apply_excess_to_forward_return

            idx_fr = None
            j0 = idx_by_d.get(decision_date) if decision_date else None
            if j0 is not None:
                idx_fr = _forward_return(index_bars, j0, horizon_days)
            fr = apply_excess_to_forward_return(fr, idx_fr, excess_mode="index")
            if fr is None:
                continue

        row = _research_sub_scores(
            window,
            quote=quote,
            index_bars=idx_slice,
            fundamentals=_fund_for(decision_date),
            sentiment=_sent_for(decision_date),
            factor_names=factor_names,
        )
        if not any(v is not None for v in row.values()):
            continue
        xs.append(row)
        ys.append(fr)
        dates.append(decision_date)

    return xs, ys, dates


def feature_fill_rates(xs: Sequence[dict], keys: Sequence[str]) -> Dict[str, Any]:
    """面板特征非空率。"""
    rows = [r for r in xs if isinstance(r, dict)]
    n = len(rows)
    out: Dict[str, Any] = {"n": n, "keys": {}}
    for k in keys:
        filled = sum(1 for r in rows if r.get(k) is not None and r.get(k) != "")
        out["keys"][str(k)] = {
            "filled": filled,
            "rate": round(filled / float(n), 4) if n else None,
        }
    return out


__all__ = [
    "collect_oo_panel",
    "feature_fill_rates",
]
