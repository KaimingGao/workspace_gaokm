"""纸面可实现回放：每个交易日按调仓钟走 rank_lots（y_oo_fuse=w_oo·((ŷ_oo+1)/(1+rot)−1)+w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1) · 按金额换算股数）。

ŷ_oo 始终是 09:30 开盘信息集。ŷ_τc：09:30 成交钟用开盘 Z（``use_minute_tau=False``）；
09:35–10:00 用 ``refresh_tau_heads_after_prefix``（5m 前缀 + 重挂 τc_rank；
z 缩可交易，避 B/C 开盘特征混 pool μ/σ）。对照列 ŷ_hl 走开盘 Z 挂上；
成交钟>09:30 随前缀重算。不挂 ŷ_τw / ŷ_τ30/60/90（调仓回测不用）。
开盘包 / 缓存 / 前缀三态见 ``replay_scoring`` 模块文档与 ``quant.md``。
成交价：09:30 用日 K 开盘（服务层不读整池 5m；有分钟时仍可用窗内首根开盘）；其后用该档 5 分钟 K 收盘。
无分钟时 09:30 回退日 K 开盘。其它钟：买入缺该根则跳过；清仓按该钟之后～10:00
下一根，再回退 09:30 / 日开盘（避免缺上午 K 把底仓拿到收盘）。
日分价闸只用 09:30–10:00 窗口内的分钟（尾盘残缺仓不当开盘锚）。
有窗口分钟时复用做 T ``resolve_t0_price_space``：|日开/分开−1| 或 |日昨/分昨−1|
超阈则该票当日 skip（``price_space_mismatch``）；比的是开/昨收锚，不是成交价。
live Follow 不走此闸。

与 ``topk_research``（独立腿聚合）并列。本金默认 20 万（表单可改）、不留现金地板（买到现金不够为止）；T+1 仍生效。
历史回测金额默认 1 万 / 2 万（live 读已保存 lot_base_amount/lot_strong_amount，缺省 1 万/2 万）。
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Callable, Collection, Dict, List, Optional

from core.backtest.replay_calendar import (
    _bars_by_date,
    _common_dates,
    _coverage_dates,
    _replay_calendar,
)
from core.backtest.replay_contrib import (
    DAY_LEG_TOP,
    _bar_px,
    _fpx,
    _holding_snap,
    _leg_stock_name,
    _lookup_quote,
    _quote_open_px,
    _resolve_mark_px,
    _stamp_stock_names,
    _stock_contrib_acc,
    _usable_stock_name,
    _watching_name_map,
    accumulate_day_contrib,
    day_stock_legs,
    finalize_stock_contrib,
    overlay_session_day_bars,
    replay_session_day,
)
from core.backtest.replay_fill import (
    LIVE_FILL_CLOCK,
    LIVE_FILL_CLOCKS,
    REPLAY_AMOUNT_BASE,
    REPLAY_AMOUNT_MAX,
    REPLAY_AMOUNT_MIN,
    REPLAY_AMOUNT_STRONG,
    REPLAY_CASH_FLOOR,
    REPLAY_FILL_CLOCK,
    REPLAY_FILL_CLOCKS,
    REPLAY_FUSE_ENTER,
    REPLAY_FUSE_STRONG,
    REPLAY_FUSION_W_NOWCAST,
    REPLAY_FUSION_W_TRADE,
    REPLAY_INITIAL_CASH,
    REPLAY_INITIAL_CASH_MAX,
    REPLAY_INITIAL_CASH_MIN,
    _apply_price_space_plan_skips,
    _clamp_fill_clock,
    _load_replay_price_space_cfg,
    _minute_bar_hm,
    _sell_fill_fallback_note,
    _stamp_replay_prev_close,
    clamp_live_fill_clock,
    clamp_replay_amount,
    clamp_replay_amount_pair,
    clamp_replay_fill_clock,
    clamp_replay_initial_cash,
    replay_fill_px,
    replay_price_space_cfg,
    replay_price_space_check,
    replay_sell_fill_px,
)
from core.backtest.replay_minute import (
    _local_minute_by_date,
    _replay_window_minutes,
    load_replay_minute_bars,
)
from core.backtest.replay_scoring import (
    _OC_PREFIX_KEYS,
    _apply_prefix_oc,
    _attach_open_yhat_heads,
    _items_from_injected_scores,
    _load_replay_global_model,
    _make_batch_query,
    _make_fill_batch_query,
    _replay_hist_and_day_bar,
    _required_factor_keys_from_return_models,
    _score_codes_fingerprint,
    _score_open_day,
    _stamp_tradable_hard_reject,
    _window_for_code,
    maybe_backfill_open_tc_rank,
    mock_quote_from_bar,
    refresh_tau_heads_after_prefix,
    rescore_replay_y_τc_at_clock,  # 门面 re-export（单测 / 冻结 API；见 pyproject F401）
    stamp_tau_prefix_misses,
)
from core.backtest.replay_sim_rows import (
    _DEBUG_SKIP_KEYS,
    _REPLAY_TAU_HORIZON_KEYS,
    _bar_date_key,
    _copy_r_tau_pred,
    _default_paper,
    _fnum,
    _hold_src_for_code,
    _hold_to_sim,
    _infer_skip_side,
    _is_debug_skip,
    _ledger_trades_to_sim,
    _merge_aux_yhat,
    _next_bar_after,
    _pct_ret,
    _px,
    _row_as_of_day,
    _sim_y_co,
    _sim_y_τc,
    _skip_to_sim,
    _strip_replay_tau_horizons,
    fuse_decision_return,
    fuse_hit_metrics,
    realized_yhat_windows,
    stamp_r_tau_on_row,
)

logger = logging.getLogger(__name__)

ENGINE_ID = "paper_replay"


def backtest_paper_replay(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: Optional[int] = None,
    min_history: int = 12,
    max_window: int = 30,
    lookback: Optional[int] = None,
    initial_cash: float = REPLAY_INITIAL_CASH,
    cost_model: str = "simple_cn",
    scores_by_date: Optional[Dict[str, List[dict]]] = None,
    yhat_horizon_days: int = 1,
    progress_cb: Optional[Callable[..., Any]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    cash_floor: Optional[float] = None,
    fuse_enter: Optional[float] = None,
    fuse_strong: Optional[float] = None,
    fusion_w_co: Optional[float] = None,
    fusion_w_oo: Optional[float] = None,
    fusion_w_oc: Optional[float] = None,
    include_session_day: bool = True,
    session_quotes: Optional[Dict[str, dict]] = None,
    session_now: Optional[datetime] = None,
    fill_clock: str = REPLAY_FILL_CLOCK,
    minute_bars_by_code: Optional[Dict[str, Dict[str, List[dict]]]] = None,
    lot_base_amount: Optional[float] = None,
    lot_strong_amount: Optional[float] = None,
    y_oo_gt0: Optional[bool] = None,
    y_τc_gt0: Optional[bool] = None,
    oo_rank_max: Optional[int] = None,
    oo_rank_top_frac: Optional[float] = None,
    price_space_cfg: Optional[dict] = None,
    score_model_role: Optional[str] = None,
    score_backend: Optional[str] = None,
    tradable_codes: Optional[Collection[str]] = None,
    tradable_reject_reason: str = "可预测性分档外",
) -> Dict[str, Any]:
    """策略调仓历史回测：ŷ_oo 09:30 开盘；ŷ_τc 随成交钟前缀重算（09:30=开盘 Z），按调仓钟 5m 价成交。

    ``score_backend`` 恒为 ridge（影子树头已下线）；传入 tree 会被忽略。
    ŷ_oo_rank 有模型时按当日截面编成 1..n 名次透传到成交行；过入场后买序按 oo_rank。
    ``tradable_codes`` 给定时：主头与排序头截面 z 仍用整池；ŷ 与名次只写这份名单（分档内）。
    排序头 ``train_universe=tier_a``（或旧 ``zscore_universe=tier_a``）只影响拟合损失，打分不再缩 z。
    ``oo_rank_max`` 开则入场另须 max(y_oo_rank, y_τc_rank) < max（两侧都缺不拦）；否则可读旧 ``oo_rank_top_frac``。
    """
    from core.research.holdout import (
        current_scoring_model_role,
        normalize_backtest_model_role,
        scoring_model_role_context,
    )
    from core.research.return_tree import (
        current_rebalance_score_backend,
        normalize_rebalance_score_backend,
        rebalance_score_backend_context,
    )

    requested = normalize_backtest_model_role(score_model_role)
    requested_backend = normalize_rebalance_score_backend(score_backend)
    if (
        current_scoring_model_role() != requested
        or current_rebalance_score_backend() != requested_backend
    ):
        import inspect

        allowed = inspect.signature(backtest_paper_replay).parameters
        params = {
            k: v for k, v in locals().items() if k in allowed and k != "stock_bars"
        }
        params["score_model_role"] = requested
        params["score_backend"] = requested_backend
        with scoring_model_role_context(requested), rebalance_score_backend_context(requested_backend):
            return backtest_paper_replay(stock_bars, **params)
    from core.backtest.engine import _trade_metrics
    from core.paper.rebalance.rank_lots import (
        DEFAULT_FUSION_W_CO,
        clamp_fusion_w_co,
        clamp_fusion_weight,
        coerce_fuse_threshold,
        get_rank_lot_cfg,
        oo_rank_max_from_cfg,
        oo_rank_top_frac_from_cfg,
        plan_rank_lot_day,
    )
    from core.paper.rebalance.watching_matrix import _apply_matrix_trades
    from core.paper.replay_ctx import paper_replay_context

    if not stock_bars or len(stock_bars) < 1:
        return {"success": False, "error": "stock_bars 为空", "params": {"engine": ENGINE_ID}}

    clock = clamp_replay_fill_clock(fill_clock)
    minute_maps = minute_bars_by_code if isinstance(minute_bars_by_code, dict) else {}
    ps_cfg = _load_replay_price_space_cfg(price_space_cfg)

    session_day = None
    n_overlay = 0
    if include_session_day:
        stock_bars, session_day, n_overlay = overlay_session_day_bars(
            stock_bars,
            now=session_now,
            quotes_by_code=session_quotes,
            fetch_quotes=session_quotes is None,
            fill_clock=clock,
        )

    date_maps = {str(c): _bars_by_date(bars) for c, bars in stock_bars.items()}

    # 先解析 ŷ 模型 → required_factor_keys / alpha_oo 抬窗，再铺日历垫特征窗
    global_model: Any = None
    co_model_doc = None
    tau_model_doc = None
    required_factor_keys: List[str] = []
    if scores_by_date is None:
        global_model = _load_replay_global_model()
        required_factor_keys = _required_factor_keys_from_return_models(global_model)
        try:
            from core.signal.factors.alpha_oo import bump_window_for_alpha_oo

            min_history, max_window = bump_window_for_alpha_oo(
                required_factor_keys,
                min_history=int(min_history),
                max_window=int(max_window),
            )
        except Exception:  # noqa: BLE001
            logger.debug("replay alpha_oo window bump failed", exc_info=True)
        try:
            from core.research.co_ridge import load_co_model

            co_model_doc = load_co_model()
        except Exception:  # noqa: BLE001
            logger.debug("load_co_model failed in paper_replay", exc_info=True)
        try:
            from core.research.tc_ridge import load_tau_model

            tau_model_doc = load_tau_model()
        except Exception:  # noqa: BLE001
            logger.debug("load_tau_model failed in paper_replay", exc_info=True)

    tc_rank_doc = None
    try:
        from core.research.holdout import current_scoring_model_role
        from core.research.tc_rank import load_tc_rank_model

        prefer_research = current_scoring_model_role() != "live"
        # 与 oo_rank 同：所选槽缺失则不挂 τc_rank，不换另一槽。
        tc_rank_doc = load_tc_rank_model(prefer_research=prefer_research)
    except Exception:  # noqa: BLE001
        logger.debug("load tc_rank model failed in paper_replay", exc_info=True)

    dates, trade_start = _replay_calendar(
        stock_bars,
        lookback=lookback,
        min_history=min_history,
        max_window=max_window,
    )
    if session_day and session_day not in dates:
        dates = sorted(dates + [session_day])
    # 消费只读：评分日历截到本地已入库的完整日线。15:05 后时钟会落到当天，
    # 供应商日线还没进仓时停在末根，避免把未入库的会话日送进 L1/L2 硬闸。
    as_of = None
    if scores_by_date is None:
        try:
            from core.research.materialize.consumer import consumer_read_only
            from core.research.materialize.coverage import (
                clip_replay_score_dates,
                complete_daily_as_of,
            )

            if consumer_read_only():
                as_of = complete_daily_as_of(
                    [str(c).strip() for c in stock_bars if str(c).strip()],
                    now=session_now,
                )
                clipped = clip_replay_score_dates(
                    dates, as_of=as_of, now=session_now
                )
                if len(clipped) < len(dates):
                    logger.info(
                        "replay calendar clip after as_of=%s (%s -> %s days)",
                        as_of or "—",
                        len(dates),
                        len(clipped),
                    )
                    dates = clipped
                    if session_day and as_of and session_day > as_of:
                        session_day = None
                        n_overlay = 0
        except Exception:  # noqa: BLE001
            logger.debug("replay calendar as_of clip failed", exc_info=True)
    n = len(dates)
    if lookback is not None:
        score_from = max(1, int(trade_start))
    else:
        score_from = max(1, int(min_history))
    if score_from >= n:
        score_from = max(1, n - 1)
    if scores_by_date is None:
        from core.research.materialize.coverage import replay_coverage_gap

        gap = replay_coverage_gap(
            stock_bars,
            dates,
            score_from=score_from,
            fill_clock=clock,
            as_of=as_of,
            now=session_now,
        )
        if gap is not None:
            return gap

    score_cache_key: Optional[str] = None
    day_inputs_index = None
    score_cache_stats: Dict[str, Any] = {
        "enabled": False,
        "hits": 0,
        "misses": 0,
        "writes": 0,
    }
    if scores_by_date is None:
        try:
            from core.backtest.replay_score_cache import (
                DayOpenInputsIndex,
                build_run_key_for_bars,
                write_run_meta,
            )
            from core.backtest.replay_score_cache import (
                enabled as replay_score_cache_enabled,
            )

            if replay_score_cache_enabled():
                info = build_run_key_for_bars(
                    stock_bars,
                    score_model_role=current_scoring_model_role(),
                    score_backend=current_rebalance_score_backend(),
                    max_window=int(max_window),
                    horizon_days=max(1, int(yhat_horizon_days or 1)),
                    required_factor_keys=required_factor_keys or None,
                    score_codes_fp=_score_codes_fingerprint(tradable_codes),
                )
                score_cache_key = str(info.get("run_key") or "") or None
                if score_cache_key:
                    day_inputs_index = DayOpenInputsIndex(
                        codes=list(stock_bars.keys()),
                        dates=dates,
                        date_maps=date_maps,
                        max_window=int(max_window),
                    )
                    score_cache_stats = {
                        "enabled": True,
                        "hits": 0,
                        "misses": 0,
                        "writes": 0,
                        "run_key": score_cache_key,
                        "codes_fp": info.get("codes_fp"),
                        "bars_fp": info.get("bars_fp"),
                        "model_fp": info.get("model_fp"),
                    }
                    write_run_meta(
                        score_cache_key,
                        {
                            "score_model_role": current_scoring_model_role(),
                            "score_backend": current_rebalance_score_backend(),
                            "max_window": int(max_window),
                            "horizon_days": max(1, int(yhat_horizon_days or 1)),
                            "n_codes": len(stock_bars),
                            "codes_fp": info.get("codes_fp"),
                            "lookback": int(lookback) if lookback is not None else None,
                        },
                    )
        except Exception:  # noqa: BLE001
            logger.debug("replay score cache init failed", exc_info=True)
            score_cache_key = None
            day_inputs_index = None
            score_cache_stats = {
                "enabled": False,
                "hits": 0,
                "misses": 0,
                "writes": 0,
            }
    need = max(2, int(min_history) + 1)
    if n < need:
        return {
            "success": False,
            "error": f"可交易日不足 {n}<{need}",
            "params": {"engine": ENGINE_ID, "common_dates": n, "lookback": lookback},
        }

    from core.watching.store import WATCHING_MAX_SIZE

    universe_n = max(1, len(stock_bars))
    cap = max(universe_n, int(WATCHING_MAX_SIZE))
    if top_k is None:
        top_k = universe_n
    else:
        top_k = max(1, min(int(top_k or 1), cap))
    floor = float(REPLAY_CASH_FLOOR if cash_floor is None else cash_floor)
    initial_cash = clamp_replay_initial_cash(initial_cash)
    alpha = clamp_fusion_w_co(
        DEFAULT_FUSION_W_CO if fusion_w_co is None else fusion_w_co
    )
    raw_oo = fusion_w_oo
    raw_oc = fusion_w_oc
    w_oo = clamp_fusion_weight(
        REPLAY_FUSION_W_TRADE if raw_oo is None else raw_oo,
        REPLAY_FUSION_W_TRADE,
    )
    w_τc = clamp_fusion_weight(
        REPLAY_FUSION_W_NOWCAST if raw_oc is None else raw_oc,
        REPLAY_FUSION_W_NOWCAST,
    )
    enter = coerce_fuse_threshold(
        REPLAY_FUSE_ENTER if fuse_enter is None else fuse_enter,
        REPLAY_FUSE_ENTER,
    )
    strong = coerce_fuse_threshold(
        REPLAY_FUSE_STRONG if fuse_strong is None else fuse_strong,
        REPLAY_FUSE_STRONG,
    )
    enter = max(0.0, min(1.0, float(enter)))
    strong = max(0.0, min(1.0, float(strong)))
    if strong < enter:
        strong = enter
    lot_base_n, lot_strong_n = clamp_replay_amount_pair(
        lot_base_amount,
        lot_strong_amount,
    )
    gt0_τc = y_τc_gt0
    paper = _default_paper(
        initial_cash=initial_cash,
        top_k=top_k,
        cost_model=cost_model,
    )
    # 必须写 rank_lots：get_path_matrix_cfg 优先该键。只写旧键 path_matrix 时，
    # 其它 live 缺省（如持仓市值帽）会漏进合成 paper。
    replay_lots = {
        "enabled": True,
        "mode": "rank_lots",
        "fuse_enter": enter,
        "fuse_strong": strong,
        "cash_floor": floor,
        "holdings_mv_cap": 0.0,
        "fusion_w_co": alpha,
        "fusion_w_oo": w_oo,
        "fusion_w_oc": w_τc,
        "y_oo_gt0": False if y_oo_gt0 is None else bool(y_oo_gt0),
        "y_τc_gt0": False if gt0_τc is None else bool(gt0_τc),
        "oo_rank_max": oo_rank_max_from_cfg(
            {"oo_rank_max": oo_rank_max} if oo_rank_max is not None else {}
        ),
        "oo_rank_top_frac": oo_rank_top_frac_from_cfg(
            {"oo_rank_top_frac": oo_rank_top_frac}
            if oo_rank_top_frac is not None
            else {}
        ),
        "lot_base_amount": lot_base_n,
        "lot_strong_amount": lot_strong_n,
    }
    paper.setdefault("rules", {})["execution"] = {
        "rebalance_timing": {
            "rank_lots": dict(replay_lots),
            "path_matrix": dict(replay_lots),
        }
    }
    rl_cfg = get_rank_lot_cfg(paper, top_k=top_k)
    rl_cfg["cash_floor"] = floor
    rl_cfg["lot_base_amount"] = lot_base_n
    rl_cfg["lot_strong_amount"] = lot_strong_n
    rl_cfg["fusion_w_co"] = alpha
    rl_cfg["fusion_w_oo"] = w_oo
    rl_cfg["fusion_w_oc"] = w_τc
    rl_cfg["fuse_enter"] = enter
    rl_cfg["fuse_strong"] = strong
    rl_cfg["y_oo_gt0"] = replay_lots["y_oo_gt0"]
    rl_cfg["y_τc_gt0"] = replay_lots["y_τc_gt0"]
    rl_cfg["oo_rank_max"] = replay_lots.get("oo_rank_max")
    rl_cfg["oo_rank_top_frac"] = replay_lots.get("oo_rank_top_frac")
    rl_cfg["score_backend"] = current_rebalance_score_backend()
    rl_cfg["holdings_mv_cap"] = 0.0
    rl_cfg["t0_sell_blocks"] = {}

    cfg = None
    try:
        from core.signal.config import load_signal_config

        cfg = load_signal_config()
        cfg = dict(cfg)
        scoring = dict(cfg.get("scoring") or {})
        scoring["skip_minute_io"] = True
        cfg["scoring"] = scoring
    except Exception:  # noqa: BLE001
        logger.debug("load_signal_config failed in paper_replay", exc_info=True)

    equity_curve: List[dict] = []
    day_returns: List[float] = []
    rebalance_logs: List[dict] = []
    debug_skips: List[dict] = []
    debug_holds: List[dict] = []
    constraints: Dict[str, int] = {
        "t1_blocks": 0,
        "cash_floor_skips": 0,
        "rebalance_days": 0,
        "minute_skips": 0,
        "minute_fills": 0,
        "tau_prefix_rescored": 0,
        "tau_prefix_miss": 0,
        "price_space_skips": 0,
        "price_space_mismatch_seen": 0,
        "sell_px_fallback": 0,
    }
    tau_pool_by_date: Dict[str, Dict[str, Any]] = {}
    if scores_by_date is None:
        if progress_cb is not None:
            try:
                progress_cb("构建 τ 截面池…", 0, 1)
            except Exception:  # noqa: BLE001
                logger.debug("paper_replay progress_cb failed", exc_info=True)
        try:
            from core.t0.score_policy import build_tau_pool_by_date, seed_tau_cross_section_pool

            tau_pool_by_date = build_tau_pool_by_date(stock_bars) or {}
            seed_tau_cross_section_pool(tau_pool_by_date)
        except Exception:  # noqa: BLE001
            logger.debug("replay tau pool build failed", exc_info=True)
            tau_pool_by_date = {}
    try:
        from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC, family_for_tc
        from core.research.lake_replay import clear_coverage_cache, prefetch_l1_coverage

        clear_coverage_cache()
        fams = [FAMILY_OO, FAMILY_TC, FAMILY_CO]
        # 盘中成交钟：额外预热 tc_HHMM，供前缀 ŷ_τc hydrate
        if str(clock or "")[:5] not in ("", "09:30"):
            fams.append(family_for_tc(tau_hm=clock))
        prefetch_l1_coverage(list((stock_bars or {}).keys()), families=fams)
    except Exception:  # noqa: BLE001
        logger.debug("replay l1 coverage prefetch failed", exc_info=True)
    if progress_cb is not None:
        try:
            progress_cb("解析股票名称…", 0, 1)
        except Exception:  # noqa: BLE001
            logger.debug("paper_replay progress_cb failed", exc_info=True)
    name_by_code = _watching_name_map()
    stock_acc: Dict[str, dict] = {}
    prev_equity = float(initial_cash)
    if lookback is not None:
        first_i = max(1, int(trade_start))
    else:
        first_i = max(1, int(min_history))
    if first_i >= n:
        first_i = max(1, n - 1)
    if dates:
        equity_curve.append(
            {
                "date": dates[first_i - 1],
                "equity": prev_equity,
                "equity_nav": 100.0,
                "return_pct": 0.0,
            }
        )

    for i in range(first_i, n):
        day = dates[i]
        if cancel_cb is not None:
            try:
                if cancel_cb():
                    return {
                        "success": False,
                        "error": "已取消",
                        "cancelled": True,
                    }
            except Exception:  # noqa: BLE001
                logger.debug("paper_replay cancel_cb failed", exc_info=True)
        if progress_cb is not None:
            try:
                progress_cb(f"{day} {clock}", i - first_i + 1, max(1, n - first_i))
            except Exception:  # noqa: BLE001
                logger.debug("paper_replay progress_cb failed", exc_info=True)

        if scores_by_date is not None:
            scored = _items_from_injected_scores(scores_by_date.get(day) or [])
            held_codes = [
                str(h.get("stock_code") or "")
                for h in (paper.get("holdings") or [])
            ]
            have = {str(it.get("stock_code") or "") for it in scored}
            for code in held_codes:
                if code and code not in have:
                    # 当日名单没有该票：不沿用昨日 ŷ（与真实打分路径一致）。
                    scored.append({"stock_code": code})
        else:
            scored = None
            day_inputs_fp = ""
            if score_cache_key and day_inputs_index is not None:
                try:
                    from core.backtest.replay_score_cache import load_day, save_day

                    day_inputs_fp = day_inputs_index.fingerprint(i)
                    scored = load_day(
                        score_cache_key, day, inputs_fp=day_inputs_fp
                    )
                    if scored is not None:
                        score_cache_stats["hits"] = (
                            int(score_cache_stats.get("hits") or 0) + 1
                        )
                    else:
                        score_cache_stats["misses"] = (
                            int(score_cache_stats.get("misses") or 0) + 1
                        )
                except Exception:  # noqa: BLE001
                    logger.debug("replay score cache load day failed", exc_info=True)
                    scored = None
            if scored is None:
                scored = _score_open_day(
                    stock_bars=stock_bars,
                    dates=dates,
                    date_maps=date_maps,
                    day_i=i,
                    max_window=max_window,
                    horizon_days=max(1, int(yhat_horizon_days or 1)),
                    cfg=cfg,
                    global_model=global_model,
                    co_model_doc=co_model_doc,
                    tau_model_doc=tau_model_doc,
                    tau_pool_day=tau_pool_by_date.get(day) or {},
                    required_factor_keys=required_factor_keys or None,
                    score_codes=tradable_codes,
                )
                if score_cache_key and day_inputs_index is not None:
                    try:
                        from core.backtest.replay_score_cache import save_day

                        if not day_inputs_fp:
                            day_inputs_fp = day_inputs_index.fingerprint(i)
                        if save_day(
                            score_cache_key,
                            day,
                            scored,
                            inputs_fp=day_inputs_fp,
                        ):
                            score_cache_stats["writes"] = (
                                int(score_cache_stats.get("writes") or 0) + 1
                            )
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "replay score cache save day failed", exc_info=True
                        )
            have = {str(it.get("stock_code") or "") for it in scored}
            for h in paper.get("holdings") or []:
                if not isinstance(h, dict):
                    continue
                code = str(h.get("stock_code") or "").strip()
                if not code or code in have:
                    continue
                # 当日未打上分不要沿用昨日 ŷ，否则明细预估值会冻住。
                scored.append({"stock_code": code})
                have.add(code)
            if clock != "09:30":
                # 晚钟：前缀 ŷ_τc + 重挂 τc_rank（z 缩可交易，避混合钟）
                scored, n_pref = refresh_tau_heads_after_prefix(
                    scored,
                    clock=clock,
                    dates=dates,
                    date_maps=date_maps,
                    minute_maps=minute_maps,
                    max_window=max_window,
                    day_i=i,
                    cfg=rl_cfg,
                    tau_pool_day=tau_pool_by_date.get(day) or {},
                    score_codes=tradable_codes,
                    tc_rank_doc=tc_rank_doc,
                )
                constraints["tau_prefix_rescored"] = int(
                    constraints.get("tau_prefix_rescored") or 0
                ) + int(n_pref)
                constraints["tau_prefix_miss"] = int(
                    constraints.get("tau_prefix_miss") or 0
                ) + int(stamp_tau_prefix_misses(scored))
            elif tc_rank_doc is not None:
                # 09:30：名次应在开盘包/缓存；可交易集全缺时才补挂
                scored = maybe_backfill_open_tc_rank(
                    scored,
                    tc_rank_doc=tc_rank_doc,
                    score_codes=tradable_codes,
                )
        # 缓存命中路径不经 _attach_open_yhat_heads；补挂后也再清一次档外头。
        try:
            from core.signal.cross_section_rescore import drop_unscored_heads

            drop_unscored_heads(scored, tradable_codes)
        except Exception:  # noqa: BLE001
            logger.debug("replay drop unscored heads failed", exc_info=True)
        _stamp_tradable_hard_reject(
            scored,
            tradable_codes,
            reason=tradable_reject_reason,
        )
        _stamp_stock_names(scored, name_by_code)
        _stamp_stock_names(paper.get("holdings") or [], name_by_code)

        prices: Dict[str, float] = {}
        sell_prices: Dict[str, float] = {}
        sell_fallback_src: Dict[str, str] = {}
        opens: Dict[str, float] = {}
        seen_px: set = set()
        mismatch_reason: Dict[str, str] = {}
        for code in list(stock_bars.keys()) + [
            str(h.get("stock_code") or "") for h in (paper.get("holdings") or [])
        ]:
            c = str(code or "").strip()
            if not c or c in seen_px:
                continue
            seen_px.add(c)
            bar = (date_maps.get(c) or {}).get(day)
            if not bar:
                continue
            open_px = _px(bar, "open")
            if open_px:
                opens[c] = open_px
            mins = (minute_maps.get(c) or {}).get(day) or []
            day_bar = _stamp_replay_prev_close(
                bar,
                date_maps=date_maps,
                dates=dates,
                code=c,
                day_i=i,
            )
            window_mins = _replay_window_minutes(mins)
            skip_ps, seen_ps = replay_price_space_check(day_bar, window_mins, ps_cfg)
            if seen_ps:
                constraints["price_space_mismatch_seen"] += 1
            if skip_ps:
                mismatch_reason[c] = skip_ps
                constraints["price_space_skips"] += 1
                continue
            px = replay_fill_px(
                daily_bar=bar, minute_bars=window_mins, fill_clock=clock
            )
            if px is not None and px > 0:
                prices[c] = px
                if window_mins:
                    constraints["minute_fills"] += 1
            elif clock != "09:30":
                constraints["minute_skips"] += 1
            sell_px, sell_src = replay_sell_fill_px(
                daily_bar=bar, minute_bars=window_mins, fill_clock=clock
            )
            if sell_px is not None and sell_px > 0:
                sell_prices[c] = sell_px
                if sell_src and sell_src != "clock":
                    sell_fallback_src[c] = sell_src

        cash = float(paper.get("cash") or 0)
        start_snap = _holding_snap(paper.get("holdings") or [])
        fill_px_map = {**sell_prices, **prices}
        with paper_replay_context(
            as_of=day,
            batch_query=_make_fill_batch_query(fill_px_map, date_maps, dates, day),
        ):
            plan = plan_rank_lot_day(
                scored=scored,
                holdings=list(paper.get("holdings") or []),
                cash=cash,
                prices=prices,
                sell_prices=sell_prices,
                opens=opens,
                cfg=rl_cfg,
                as_of=day,
            )
            _apply_price_space_plan_skips(plan, mismatch_reason)
            for leg in plan.get("sells") or []:
                if not isinstance(leg, dict):
                    continue
                c = str(leg.get("stock_code") or "").strip()
                src = sell_fallback_src.get(c) or ""
                note = _sell_fill_fallback_note(clock, src)
                if note:
                    reason = str(leg.get("reason") or "").strip()
                    if note not in reason:
                        leg["reason"] = f"{reason} · {note}" if reason else note
                    leg["fill_fallback"] = src
                    constraints["sell_px_fallback"] += 1
            sell_trades = []
            buy_trades = []
            for leg in plan.get("sells") or []:
                px = sell_prices.get(str(leg.get("stock_code") or ""))
                if not px:
                    px = prices.get(str(leg.get("stock_code") or ""))
                if not px:
                    continue
                sell_trades.append(
                    {**leg, "price": px, "amount": round(float(leg["shares"]) * px, 2)}
                )
            for leg in plan.get("buys") or []:
                px = prices.get(str(leg.get("stock_code") or ""))
                if not px:
                    continue
                buy_trades.append(
                    {**leg, "price": px, "amount": round(float(leg["shares"]) * px, 2)}
                )
            for sk in plan.get("skips") or []:
                reason = str(sk.get("reason") or "")
                if "地板" in reason or "现金不足" in reason:
                    constraints["cash_floor_skips"] += 1
                if "T+1" in str(sk.get("reason") or ""):
                    constraints["t1_blocks"] += 1

            applied = {"sell_trades": [], "buy_trades": [], "apply_skips": []}
            if sell_trades or buy_trades:
                applied = _apply_matrix_trades(paper, sell_trades, buy_trades, as_of=day)
                constraints["rebalance_days"] += 1
            day_skip_sell: set = set()
            for sk in list(plan.get("skips") or []) + list(applied.get("apply_skips") or []):
                if not isinstance(sk, dict):
                    continue
                debug_skips.append(_skip_to_sim(sk, day))
                if _infer_skip_side(sk) == "sell":
                    c = str(sk.get("stock_code") or "").strip()
                    if c:
                        day_skip_sell.add(c)
            traded_codes = {
                str(t.get("stock_code") or "").strip()
                for t in list(applied.get("sell_trades") or [])
                + list(applied.get("buy_trades") or [])
                if str(t.get("stock_code") or "").strip()
            }
            for h in paper.get("holdings") or []:
                if not isinstance(h, dict):
                    continue
                code = str(h.get("stock_code") or "").strip()
                if not code or code in traded_codes or code in day_skip_sell:
                    continue
                debug_holds.append(
                    _hold_to_sim(
                        _hold_src_for_code(code, plan=plan, scored=scored),
                        day,
                        h,
                        date_maps=date_maps,
                        prices=prices,
                    )
                )

        equity = float(paper.get("cash") or 0)
        for h in paper.get("holdings") or []:
            if not isinstance(h, dict):
                continue
            code = str(h.get("stock_code") or "").strip()
            sh = float(h.get("shares") or 0)
            if not code or sh <= 0:
                continue
            close_px = _resolve_mark_px(
                code=code,
                day=day,
                date_maps=date_maps,
                dates=dates,
                day_i=i,
                holding=h,
            )
            if close_px is not None and close_px > 0:
                h["last_price"] = float(close_px)
                equity += sh * float(close_px)
        ret = (equity / prev_equity - 1.0) * 100.0 if prev_equity > 0 else 0.0
        day_returns.append(ret)
        cash_now = round(float(paper.get("cash") or 0), 2)
        nav = (
            round(100.0 * equity / float(initial_cash), 4)
            if initial_cash
            else None
        )
        n_holdings = len(paper.get("holdings") or [])
        end_snap = _holding_snap(paper.get("holdings") or [])
        all_legs, _ = day_stock_legs(
            start=start_snap,
            end=end_snap,
            date_maps=date_maps,
            prev_day=dates[i - 1],
            day=day,
            open_px=prices,
            prev_equity=prev_equity,
            name_by_code=name_by_code,
            top=0,
        )
        accumulate_day_contrib(stock_acc, all_legs, day=day)
        legs = all_legs[:DAY_LEG_TOP]
        legs_more = max(0, len(all_legs) - DAY_LEG_TOP)
        equity_curve.append(
            {
                "date": day,
                "equity": round(equity, 2),
                "equity_nav": nav,
                "return_pct": round(ret, 4),
                "cash": cash_now,
                "n_holdings": n_holdings,
                "legs": legs,
                "legs_more": legs_more,
            }
        )
        rebalance_logs.append(
            {
                "signal_date": day,
                "exec_date": day,
                "ok": True,
                "n_buy": len(applied.get("buy_trades") or []),
                "n_sell": len(applied.get("sell_trades") or []),
                "n_hold": len(plan.get("holds") or []),
                "n_skip": len(plan.get("skips") or []),
                "n_apply_skip": len(applied.get("apply_skips") or []),
                "cash": cash_now,
                "equity": round(equity, 2),
                "equity_nav": nav,
                "n_holdings": n_holdings,
                "return_pct": round(ret, 4),
            }
        )
        prev_equity = equity

    metrics = _trade_metrics(day_returns, holding_days=1)
    metrics = dict(metrics)
    ledger_trades = list(paper.get("trades") or [])
    cash_by_day = {
        str(p.get("date") or "")[:10]: p.get("cash")
        for p in equity_curve
        if isinstance(p, dict)
    }
    hold_by_day = {
        str(p.get("date") or "")[:10]: p.get("n_holdings")
        for p in equity_curve
        if isinstance(p, dict)
    }
    equity_by_day = {
        str(p.get("date") or "")[:10]: p.get("equity")
        for p in equity_curve
        if isinstance(p, dict)
    }
    date_index = {d: i for i, d in enumerate(dates)}

    def _stamp_day_context(row: dict) -> dict:
        day = str(row.get("as_of") or "")[:10]
        row["cash_after"] = cash_by_day.get(day)
        row["n_holdings"] = hold_by_day.get(day)
        row["equity_after"] = equity_by_day.get(day)
        code = str(row.get("stock_code") or "")
        r_cc, r_on, r_tau_open = realized_yhat_windows(
            code,
            day,
            dates=dates,
            date_maps=date_maps,
            date_index=date_index,
        )
        row["realized_cc"] = r_cc
        row["realized_on"] = r_on
        # ŷ_oo 真实 = 次日开/今日开。用 τ=open 的 close/open 与隔夜复合，勿用盘中 price(τ)。
        if r_tau_open is not None and r_on is not None:
            row["realized_oo"] = round(
                ((1.0 + float(r_tau_open) / 100.0) * (1.0 + float(r_on) / 100.0) - 1.0)
                * 100.0,
                4,
            )
        else:
            row["realized_oo"] = None
        stamp_r_tau_on_row(
            row,
            daily_bar=(date_maps.get(code) or {}).get(day),
            minute_bars=(minute_maps.get(code) or {}).get(day) or [],
            fill_clock=clock,
        )
        # ŷ_τc 真实 = close[T]/price(τ)−1（随 fill_clock）；09:30 时 price(τ)=open。
        r_tau_clock = _fnum(row.get("r_realized"))
        row["realized_tau"] = (
            r_tau_clock if r_tau_clock is not None else r_tau_open
        )
        try:
            from core.signal.yhat_windows import y_oo_fuse_realized_pct

            realized_fuse = y_oo_fuse_realized_pct(
                row.get("realized_oo"),
                open_px=row.get("day_open"),
                price_tau=row.get("rebalance_px") or row.get("price_tau"),
            )
            row["y_oo_fuse_realized"] = realized_fuse
        except Exception:  # noqa: BLE001
            logger.debug("stamp y_oo_fuse_realized failed", exc_info=True)
            row["y_oo_fuse_realized"] = None
        row["fusion_w_oo"] = w_oo
        row["fusion_w_oc"] = w_τc
        row["fusion_w_co"] = alpha
        row["fill_clock"] = clock
        # tip：open ≡ 09:30；缺钟或仍写 open 时显式落 fill_clock（如 09:30 / 09:50）。
        asof = str(row.get("as_of_tau") or row.get("rem_tau") or "").strip()
        if not asof or asof.lower() == "open":
            row["as_of_tau"] = clock
            row["rem_tau"] = clock
        return row

    sim_trades = [_stamp_day_context(r) for r in _ledger_trades_to_sim(ledger_trades)]
    sim_trades.extend(_stamp_day_context(dict(s)) for s in debug_skips)
    sim_trades.extend(_stamp_day_context(dict(s)) for s in debug_holds)
    _stamp_stock_names(sim_trades, name_by_code)
    _stamp_stock_names(ledger_trades, name_by_code)
    _stamp_stock_names(paper.get("holdings") or [], name_by_code)
    stock_contrib = finalize_stock_contrib(
        stock_acc,
        initial_cash=initial_cash,
        trades=ledger_trades,
        name_by_code=name_by_code,
    )
    hit_pct, hit_n, hit_hits = fuse_hit_metrics(sim_trades)
    dec_pct, dec_n = fuse_decision_return(sim_trades)
    metrics["trade_count"] = len(ledger_trades)
    metrics["sim_trade_count"] = len(ledger_trades)
    metrics["skip_count"] = len(debug_skips)
    metrics["hold_count"] = len(debug_holds)
    metrics["rebalance_days"] = constraints["rebalance_days"]
    metrics["day_count"] = len(day_returns)
    metrics["hit_rate_pct"] = hit_pct
    metrics["hit_n"] = hit_n
    metrics["hit_hits"] = hit_hits
    metrics["decision_return_pct"] = dec_pct
    metrics["decision_n"] = dec_n
    final_eq = float(equity_curve[-1]["equity"]) if equity_curve else float(initial_cash)
    total_ret = (final_eq / float(initial_cash) - 1.0) * 100.0 if initial_cash else 0.0
    metrics["total_return_pct"] = round(total_ret, 2)

    used_minutes = bool(minute_maps) and int(constraints.get("minute_fills") or 0) > 0
    exec_mode = "minute_5m" if used_minutes or clock != "09:30" else "open_930"
    fill_note = (
        f"成交={clock}"
        + (
            " 5m（该档收盘；缺分钟买入跳过、清仓回退其后～10:00 再回退 09:30/日开盘"
            if clock != "09:30"
            else "（日开盘；不读整池 5m；有分钟时亦可用窗内首根开盘"
        )
        + (
            "；日分价错位跳过"
            if ps_cfg.get("t0_price_space_gate") is not False
            else "；日分价闸关"
        )
        + "）"
    )
    oc_note = (
        "ŷ_τc=开盘 Z"
        if clock == "09:30"
        else f"ŷ_τc=截至 {clock} 的 5m 前缀（与做 T rescore 同路径；ŷ_oo 仍开盘）"
    )
    from core.signal.yhat_windows import FORMULA_OO_FUSE

    fuse_enter = rl_cfg.get("fuse_enter")
    note = (
        f"引擎={ENGINE_ID}：每个交易日 y_oo_fuse={FORMULA_OO_FUSE}，{fill_note}；{oc_note}；"
        f"fuse 权 w_oo={w_oo:g} w_τc={w_τc:g} w_co={alpha:g}；未过入场则已持仓清仓；"
        f"fuse 过入场（fuse入场={fuse_enter}）后按 y_oo_rank 买，缺名次才按 fuse"
        f"（开加上限=观察池 {top_k} 只，现金不够则停），"
        f"每笔 {int(rl_cfg.get('lot_base_amount') or REPLAY_AMOUNT_BASE)} 元；"
        f"不留现金地板；T+1；成本={cost_model}；≠ topk_research。"
    )
    if session_day:
        note += (
            f" 含当日 {session_day} {clock}（现价开盘 ×{n_overlay}；"
            "真实收盘/隔夜 label 待日K入库）。"
        )
    return {
        "success": True,
        "strategy": ENGINE_ID,
        "params": {
            "engine": ENGINE_ID,
            "top_k": top_k,
            "min_history": min_history,
            "max_window": max_window,
            "required_factor_keys": list(required_factor_keys),
            "initial_cash": initial_cash,
            "cash_floor": floor,
            "fuse_enter": rl_cfg.get("fuse_enter"),
            "fuse_strong": rl_cfg.get("fuse_strong"),
            "y_oo_gt0": rl_cfg.get("y_oo_gt0"),
            "y_τc_gt0": rl_cfg.get("y_τc_gt0"),
            "oo_rank_max": rl_cfg.get("oo_rank_max"),
            "oo_rank_top_frac": rl_cfg.get("oo_rank_top_frac"),
            "fusion_w_co": alpha,
            "fusion_w_oo": w_oo,
            "fusion_w_oc": w_τc,
            "lot_base_amount": float(rl_cfg.get("lot_base_amount") or REPLAY_AMOUNT_BASE),
            "lot_strong_amount": float(rl_cfg.get("lot_strong_amount") or REPLAY_AMOUNT_STRONG),
            "cost_model": cost_model,
            "execution_mode": exec_mode,
            "fill_clock": clock,
            "price_space_gate": bool(ps_cfg.get("t0_price_space_gate", True)),
            "price_space_max_dev_pct": ps_cfg.get("t0_price_space_max_dev_pct"),
            "price_space_prev_dev_pct": ps_cfg.get("t0_price_space_prev_dev_pct"),
            "minute_codes": len(minute_maps),
            "horizon_days": 1,
            "stock_count": len(stock_bars),
            "lookback": int(lookback) if lookback is not None else None,
            "score_model_role": current_scoring_model_role(),
            "score_backend": current_rebalance_score_backend(),
            "common_dates": max(0, n - first_i),
            "trade_days": max(0, n - first_i),
            "end_date": dates[-1] if dates else None,
            "session_day": session_day,
            "session_overlay_n": n_overlay,
            "yhat_horizon_days": yhat_horizon_days,
            "scores_injected": scores_by_date is not None,
            "score_cache": score_cache_stats,
            "yhat_source": "research",
            "model_role": "research",
        },
        "metrics": metrics,
        "equity_curve": equity_curve,
        "constraints_hit": constraints,
        "rebalance_logs": rebalance_logs,
        "trades": ledger_trades,
        "sim_trades": sim_trades,
        "sim_trade_count": len(ledger_trades),
        "stock_contrib": stock_contrib,
        "holdings_end": copy.deepcopy(paper.get("holdings") or []),
        "cash_end": round(float(paper.get("cash") or 0), 2),
        "paper": paper,
        "note": note,
    }

