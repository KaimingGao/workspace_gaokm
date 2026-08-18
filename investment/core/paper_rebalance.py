"""横截面驱动的纸面调仓模拟（P11.3，非实盘）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 卖出原因标识（用常量替代字符串匹配，避免文案改动导致 soft-hold 逻辑失效）
SELL_REASON_BELOW_HOLD = "below_hold"          # ŷ_trade 低于卖出门槛
SELL_REASON_NOT_IN_TOPK = "not_in_topk"        # 不在横截面 TopK
SELL_REASON_HARD_REJECT = "hard_reject"        # 硬拒绝
SELL_REASON_SENTIMENT_TRIM = "sentiment_trim"  # 舆情缩仓

# 可被 soft-hold 豁免的卖出原因
_SOFT_HOLD_ELIGIBLE_REASONS = frozenset({
    SELL_REASON_BELOW_HOLD,
    SELL_REASON_NOT_IN_TOPK,
})


def clip_shares_to_turnover_budget(
    *,
    shares: int,
    fill_px: float,
    buy_amt_so_far: float,
    buy_budget_amt: float,
    sell_amt: float,
    equity_before: float,
    max_turnover_pct: float,
) -> int:
    """按剩余换手买入预算 + 双边总上限，手数向下取整到 100 股。

    半仓重试仍可能 > 剩余预算；先裁剪可成交部分，避免预算 residual 被永久跳过。
    """
    sh = int(shares or 0)
    px = float(fill_px or 0.0)
    eq = float(equity_before or 0.0)
    if sh <= 0 or px <= 0 or eq <= 0:
        return 0
    rem_buy = max(0.0, float(buy_budget_amt) - float(buy_amt_so_far))
    # 双边：(sell + buy_after)/2/equity*100 ≤ max_to
    # → buy_after ≤ 2*(max_to/100)*equity - sell
    rem_total = max(
        0.0,
        2.0 * (float(max_turnover_pct) / 100.0) * eq
        - float(sell_amt)
        - float(buy_amt_so_far),
    )
    rem = min(rem_buy, rem_total)
    if rem <= 0:
        return 0
    max_sh = int(rem // px // 100) * 100
    return max(0, min(sh, max_sh))


def resolve_buy_turnover_budget(
    *,
    sell_trades: List[dict],
    buy_trades: List[dict],
    equity_before: float,
    max_turnover_pct: float,
) -> Tuple[float, float, float]:
    """买侧换手预算。返回 (sell_amt, buy_amt_so_far, buy_budget_amt)。

    单边 50% 给买；卖未用可溢出给买（上限再 50%）。卖腿本身不受此帽。
    """
    sell_amt = sum(float(t.get("amount") or 0) for t in (sell_trades or []))
    buy_amt_so_far = sum(float(t.get("amount") or 0) for t in (buy_trades or []))
    eq = float(equity_before or 0.0)
    _max_to = float(max_turnover_pct)
    _single_side_amt = (_max_to / 2.0) / 100.0 * eq
    _sell_excess = max(0.0, _single_side_amt - sell_amt)
    buy_budget_amt = _single_side_amt + min(_sell_excess, _single_side_amt)
    return sell_amt, buy_amt_so_far, buy_budget_amt


def select_force_trim_codes(
    holdings: List[dict],
    *,
    score_by_code: Dict[str, Any],
    top_codes: set,
    trim_count: int,
) -> List[str]:
    """分池持仓膨胀减仓：优先卸中间带（不在目标簿），再卸簿内最低分。

    避免「簿内低分被砍 → 买入腿立刻买回 → 膨胀消不掉」。
    """
    n = max(0, int(trim_count or 0))
    if n <= 0 or not holdings:
        return []
    mid: List[tuple] = []
    book: List[tuple] = []
    for h in holdings:
        code = str(h.get("stock_code") or "").strip()
        if not code:
            continue
        sc = score_by_code.get(code)
        try:
            key = float(sc) if sc is not None else float("-inf")
        except (TypeError, ValueError):
            key = float("-inf")
        (book if code in top_codes else mid).append((code, key))
    mid.sort(key=lambda x: (x[1], x[0]))
    book.sort(key=lambda x: (x[1], x[0]))
    out: List[str] = []
    seen = set()
    for code, _ in mid + book:
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
        if len(out) >= n:
            break
    return out


def select_force_trim_codes_sellable(
    holdings: List[dict],
    *,
    score_by_code: Dict[str, Any],
    top_codes: set,
    trim_count: int,
    quote_cache: Optional[Dict[str, dict]] = None,
    sell_block_fn=None,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """膨胀减仓：跳过跌停/停牌，继续选下一名可卖票。

    返回 (可卖 codes, 被挡 skips)。
    """
    n = max(0, int(trim_count or 0))
    blocked: List[Dict[str, Any]] = []
    if n <= 0 or not holdings:
        return [], blocked
    ordered = select_force_trim_codes(
        holdings,
        score_by_code=score_by_code,
        top_codes=top_codes,
        trim_count=len(holdings),  # 取全序，再按可卖性截断
    )
    out: List[str] = []
    qcache = quote_cache or {}
    for code in ordered:
        if len(out) >= n:
            break
        quote = qcache.get(code) or {}
        reason = None
        if sell_block_fn is not None:
            try:
                reason = sell_block_fn(code, quote)
            except Exception:
                reason = None
        if reason:
            blocked.append(
                {
                    "stock_code": code,
                    "reason": reason,
                    "score": score_by_code.get(code),
                    "path": "force_trim",
                }
            )
            continue
        out.append(code)
    return out, blocked



from core.paper import ORIGIN_STRATEGY, _now_iso, append_operation_log, build_ops_report
from core.paper_costs import (
    annotate_trade,
    apply_fill_price,
    calc_trade_fees,
    cost_params,
    resolve_cost_model,
)
from core.ports.market import quote_price as _quote_price


def quote_change_pct(quote: Optional[dict]) -> Optional[float]:
    """从行情 dict 解析当日涨跌幅（%）；缺则 None。"""
    if not isinstance(quote, dict):
        return None
    for key in ("change_raw", "change_pct", "pct_chg"):
        raw = quote.get(key)
        if raw is None:
            continue
        try:
            return round(float(raw), 2)
        except (TypeError, ValueError):
            continue
    raw = quote.get("change")
    if raw is None:
        return None
    try:
        return round(float(str(raw).replace("%", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def attach_change_pct_to_rebalance_report(
    report: List[dict],
    *,
    summary: Optional[dict] = None,
) -> List[dict]:
    """给调仓报告行补 ``change_pct``（相对昨收）。

    优先用 mark_to_market 持仓行；清仓/未入仓票再批量补行情。
    """
    rows = list(report or [])
    if not rows:
        return rows
    chg_by: Dict[str, float] = {}
    for h in (summary or {}).get("holdings") or []:
        if not isinstance(h, dict):
            continue
        code = str(h.get("stock_code") or "").strip()
        if not code or h.get("change_pct") is None:
            continue
        try:
            chg_by[code] = round(float(h.get("change_pct")), 2)
        except (TypeError, ValueError):
            continue
    missing = [
        str(r.get("stock_code") or "").strip()
        for r in rows
        if str(r.get("stock_code") or "").strip()
        and str(r.get("stock_code") or "").strip() not in chg_by
    ]
    if missing:
        quotes = _batch_query_quotes(missing)
        for code in missing:
            chg = quote_change_pct(quotes.get(code) or {})
            if chg is not None:
                chg_by[code] = chg
    for r in rows:
        code = str(r.get("stock_code") or "").strip()
        if code in chg_by:
            r["change_pct"] = chg_by[code]
    return rows


def _batch_query_quotes(codes: List[str], *, workers: int = 8) -> Dict[str, dict]:
    """批量行情：优先经 DataService；失败再线程池逐票。"""
    if not codes:
        return {}
    uniq = list(dict.fromkeys(c for c in codes if c))
    if not uniq:
        return {}
    try:
        from core.data.service import get_default_service

        got = get_default_service().batch_get_quotes(uniq) or {}
        # 统一成 {code: quote}；补全未返回的 key
        out: Dict[str, dict] = {}
        for c in uniq:
            q = got.get(c)
            if isinstance(q, dict) and q.get("success"):
                out[c] = q
            elif isinstance(q, dict):
                out[c] = q
        if len(out) >= max(1, len(uniq) // 2):
            return out
    except Exception:
        logger.warning("batch_query_quotes failed; fallback per-code", exc_info=True)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.data_service import get_quote

    out = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(uniq))) as pool:
        futures = {pool.submit(get_quote, c): c for c in uniq}
        try:
            for fut in as_completed(futures, timeout=12):
                code = futures[fut]
                try:
                    out[code] = fut.result(timeout=0)
                except Exception:
                    out[code] = {}
        except Exception:
            for code, fut in futures.items():
                if code in out:
                    continue
                try:
                    out[code] = fut.result(timeout=0) if fut.done() else {}
                except Exception:
                    out[code] = {}
    return out


def _buy_match_block_reason(code: str, quote: Optional[dict]) -> Optional[str]:
    """Y2.2：无价/涨停/停牌关键词 → 跳过买入原因；否则 None。"""
    q = quote or {}
    try:
        from core.backtest.matching import is_limit_up, limit_up_threshold_for_code
        from core.market_calendar import halt_hint

        blob = " ".join(
            str(q.get(k) or "")
            for k in ("status", "trade_status", "stock_name", "name", "note", "message")
        )
        hint = halt_hint(blob)
        if hint.get("possible_halt"):
            return "停牌/不可交易提示，跳过买入"

        change = q.get("change_raw")
        if change is None:
            change = q.get("change_pct")
        if change is None:
            change = q.get("pct_chg")
        prev = q.get("prev_close") or q.get("pre_close") or q.get("yesterday_close")
        price = q.get("price_raw") or q.get("price")
        if prev is not None and price is not None:
            try:
                if is_limit_up(float(prev), float(price), stock_code=code):
                    return (
                        f"疑似涨停（阈值≥{limit_up_threshold_for_code(code)}%），跳过买入"
                    )
            except (TypeError, ValueError):
                pass
        elif change is not None:
            try:
                thr = limit_up_threshold_for_code(code)
                if float(change) >= thr:
                    return f"涨跌幅 {float(change):.2f}%≥涨停阈值 {thr}%，跳过买入"
            except (TypeError, ValueError):
                pass
    except Exception:
        return None
    return None


def _sell_match_block_reason(code: str, quote: Optional[dict]) -> Optional[str]:
    """P3-3：卖出侧涨跌停/停牌检查 — 跌停/停牌无法成交则跳过卖出；否则 None。"""
    q = quote or {}
    try:
        from core.backtest.matching import is_limit_down, limit_down_threshold_for_code
        from core.market_calendar import halt_hint

        blob = " ".join(
            str(q.get(k) or "")
            for k in ("status", "trade_status", "stock_name", "name", "note", "message")
        )
        hint = halt_hint(blob)
        if hint.get("possible_halt"):
            return "停牌/不可交易提示，跳过卖出"

        change = q.get("change_raw")
        if change is None:
            change = q.get("change_pct")
        if change is None:
            change = q.get("pct_chg")
        prev = q.get("prev_close") or q.get("pre_close") or q.get("yesterday_close")
        price = q.get("price_raw") or q.get("price")
        if prev is not None and price is not None:
            try:
                if is_limit_down(float(prev), float(price), stock_code=code):
                    return (
                        f"疑似跌停（阈值≤{limit_down_threshold_for_code(code)}%），跳过卖出"
                    )
            except (TypeError, ValueError):
                pass
        elif change is not None:
            try:
                thr = limit_down_threshold_for_code(code)
                if float(change) <= thr:
                    return f"涨跌幅 {float(change):.2f}%≤跌停阈值 {thr}%，跳过卖出"
            except (TypeError, ValueError):
                pass
    except Exception:
        return None
    return None


def compute_turnover_stats(
    sell_trades: List[dict],
    buy_trades: List[dict],
    *,
    equity_before: Optional[float],
    max_turnover_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """双边换手：``(买额+卖额)/2/净值``；可选对照 ``max_turnover_pct`` 软上限。"""
    sell_amount = round(sum(float(t.get("amount") or 0) for t in sell_trades or []), 2)
    buy_amount = round(sum(float(t.get("amount") or 0) for t in buy_trades or []), 2)
    eq = float(equity_before or 0)
    turnover_pct = (
        round((sell_amount + buy_amount) / 2.0 / eq * 100.0, 2) if eq > 0 else None
    )
    max_to = None
    if max_turnover_pct is not None:
        try:
            max_to = float(max_turnover_pct)
        except (TypeError, ValueError):
            max_to = None
    over = (
        turnover_pct is not None and max_to is not None and turnover_pct > max_to + 1e-9
    )
    return {
        "buy_amount": buy_amount,
        "sell_amount": sell_amount,
        "buy_count": len(buy_trades or []),
        "sell_count": len(sell_trades or []),
        "equity_before": round(eq, 2) if eq > 0 else None,
        "turnover_pct": turnover_pct,
        "max_turnover_pct": max_to,
        "over_limit": bool(over),
        "definition": "two_way=(buy+sell)/2/equity*100",
    }


def build_rebalance_cash_impact(
    *,
    cash_before: float,
    position_count_before: int,
    sell_trades: List[dict],
    buy_trades: List[dict],
    summary: Optional[dict],
    equity_before: Optional[float] = None,
    cost_model: Optional[str] = None,
    max_turnover_pct: Optional[float] = None,
    turnover_capped: bool = False,
) -> Dict[str, Any]:
    """资金影响 + 换手摘要（预演/落账共用）。"""
    turn = compute_turnover_stats(
        sell_trades,
        buy_trades,
        equity_before=equity_before,
        max_turnover_pct=max_turnover_pct,
    )
    cash_after = float((summary or {}).get("cash") or 0)
    return {
        "cash_before": round(float(cash_before or 0), 2),
        "buy_amount": turn["buy_amount"],
        "sell_amount": turn["sell_amount"],
        "net_cash_flow": round(turn["sell_amount"] - turn["buy_amount"], 2),
        "cash_after": round(cash_after, 2),
        "position_count_before": int(position_count_before or 0),
        "position_count_after": int((summary or {}).get("position_count") or 0),
        "equity_after": (summary or {}).get("equity"),
        "equity_before": turn.get("equity_before"),
        "cost_model": cost_model,
        "turnover_pct": turn.get("turnover_pct"),
        "max_turnover_pct": turn.get("max_turnover_pct"),
        "turnover_over_limit": turn.get("over_limit"),
        "turnover_capped": bool(turnover_capped),
        "turnover_definition": turn.get("definition"),
        "buy_count": turn.get("buy_count"),
        "sell_count": turn.get("sell_count"),
    }


def simulate_cross_section_rebalance(
    paper: dict,
    ranking: List[dict],
    *,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    respect_max_positions: bool = True,
    score_lookup: Optional[List[dict]] = None,
    skip_sentiment_prior: bool = False,
) -> Dict[str, Any]:
    """
    按横截面 TopK / 分池目标簿调仓。

    - 横截面（``respect_max_positions=True``）：卖出不在 TopK，或 ŷ_trade 低于 min_hold_score；
      再从 TopK 买入（EOD≥min_score 且过 τ 闸）的未持仓。
    - 分池（``respect_max_positions=False``）：滞回——买入仍看目标簿且 ŷ_EOD≥min_score（+τ 闸）；
      **卖出仅当 ŷ_trade < min_hold_score**，不因「未进簿/截断」清仓。
      （卖/表/排序同一轴；买入门槛仍用隔夜 ŷ_EOD。）
      分池买入 sizing：``min(cash, equity × ratio)``，ratio 优先吃 optimize 目标仓，否则 1/簿长，再受 position_pct 封顶。
    买入前强制 check_account_risk；超限则拦截加仓并写 risk_block 日志。

    ``score_lookup``：可选全量打分行（含低于 min_score 未进簿的票），供卖出腿带分。
    ``skip_sentiment_prior``：确认落账复用预演簿时跳过舆情重拉（只成交）。
    """
    from core.data_service import get_quote
    from core.ports.market import quote_price

    query_quote = get_quote  # 局部兼容下文 query_quote(...) 调用

    rules = paper.get("rules") or {}
    cost_model = resolve_cost_model(paper)
    fee_params = cost_params(paper)
    max_pos = max(1, int(rules.get("max_positions") or 5))
    if top_k is None:
        top_k = max_pos
    else:
        top_k = max(1, int(top_k))
    if respect_max_positions:
        top_k = min(top_k, max_pos, 30)
    else:
        # 分池：按簿长持有；上限对齐 cluster max_names（80），勿再用 30 砍掉簿尾
        top_k = min(top_k, 80)
    if min_score is None:
        from core.signal.score_display import resolve_buy_floor, resolve_hold_floor

        min_score = resolve_buy_floor(paper, heuristic_default=55.0)
        min_hold_score = resolve_hold_floor(paper, heuristic_default=45.0)
    else:
        min_score = float(min_score)
        from core.signal.score_display import resolve_hold_floor

        min_hold_score = resolve_hold_floor(paper, heuristic_default=45.0)

    # 双轨：predicted 门槛可被 rebalance_tracks 覆盖；heuristic 在买卖循环按票解析
    try:
        from core.signal.rebalance_tracks import get_rebalance_tracks_cfg, predicted_floors

        _tracks_cfg = get_rebalance_tracks_cfg()
        _pred_buy, _pred_hold = predicted_floors(_tracks_cfg)
        if _tracks_cfg.get("predicted_buy_floor") is not None:
            min_score = float(_pred_buy)
        min_hold_score = float(_pred_hold)
    except Exception:
        _tracks_cfg = {}
    # 分池：持仓上限=簿长；买入门槛见 resolve_buy_floor / 双轨
    if not respect_max_positions:
        max_positions = top_k
    else:
        max_positions = max_pos
    position_pct = float(rules.get("position_pct") or 0.15)
    max_turnover_pct: Optional[float] = None
    raw_mto = rules.get("max_turnover_pct", rules.get("max_turnover"))
    if raw_mto is not None and raw_mto != "":
        try:
            max_turnover_pct = float(raw_mto)
        except (TypeError, ValueError):
            max_turnover_pct = None

    top_items = (ranking or [])[:top_k]
    top_codes = {str(x.get("stock_code") or "") for x in top_items if x.get("stock_code")}
    item_by_code: Dict[str, dict] = {}
    # lookup 补未进簿持仓；同码以 ranking（簿内行）覆盖，避免旧字段抢卖出门槛
    for src in list(score_lookup or []) + list(ranking or []):
        code = str(src.get("stock_code") or "")
        if code:
            item_by_code[code] = src
    # 卖出门槛 / 报告主分：ŷ_trade；买入 EOD 闸另算
    trade_score_by_code: Dict[str, float] = {}
    eod_score_by_code: Dict[str, float] = {}
    tau_by_code: Dict[str, float] = {}
    hard_reject_by_code: Dict[str, str] = {}
    for src in list(score_lookup or []) + list(ranking or []):
        code = str(src.get("stock_code") or "")
        if not code:
            continue
        if src.get("hard_reject"):
            hard_reject_by_code[code] = str(
                src.get("reject_reason") or "硬拒绝"
            )
        else:
            hard_reject_by_code.pop(code, None)
        try:
            from core.signal.rebalance_tracks import (
                TRACK_HEURISTIC,
                resolve_score_track,
            )
            from core.signal.dual_score import decision_score_for_item

            if resolve_score_track(src) == TRACK_HEURISTIC:
                from core.signal.rebalance_tracks import heuristic_score_value

                hs = heuristic_score_value(src)
                if hs is not None:
                    trade_score_by_code[code] = float(hs)
            else:
                d_sc = decision_score_for_item(src)
                if d_sc is not None:
                    trade_score_by_code[code] = float(d_sc)
                elif src.get("predicted_score_blend") is not None:
                    trade_score_by_code[code] = float(src.get("predicted_score_blend"))
                elif src.get("predicted_score") is not None:
                    trade_score_by_code[code] = float(src.get("predicted_score"))
        except (TypeError, ValueError):
            pass
        except Exception:
            try:
                from core.signal.rebalance_tracks import (
                    TRACK_HEURISTIC,
                    resolve_score_track,
                )

                if resolve_score_track(src) == TRACK_HEURISTIC:
                    from core.signal.rebalance_tracks import heuristic_score_value

                    hs = heuristic_score_value(src)
                    if hs is not None:
                        trade_score_by_code[code] = float(hs)
                elif src.get("predicted_score") is not None:
                    trade_score_by_code[code] = float(src.get("predicted_score"))
            except (TypeError, ValueError):
                pass
        try:
            from core.signal.rebalance_tracks import (
                TRACK_HEURISTIC,
                resolve_score_track,
            )
            from core.signal.dual_score import eod_gate_score_for_item

            if resolve_score_track(src) == TRACK_HEURISTIC:
                from core.signal.rebalance_tracks import heuristic_score_value

                hs = heuristic_score_value(src)
                if hs is not None:
                    eod_score_by_code[code] = float(hs)
            else:
                gate = eod_gate_score_for_item(src)
                if gate is not None:
                    eod_score_by_code[code] = float(gate)
        except (TypeError, ValueError):
            pass
        except Exception:
            pass
        try:
            from core.signal.dual_score import resolve_predicted_score_tau

            yt = resolve_predicted_score_tau(src)
            if yt is not None:
                tau_by_code[code] = float(yt)
        except Exception:
                pass
    # 兼容旧引用名：卖出主分 = ŷ_trade
    score_by_code = trade_score_by_code

    holdings = paper.get("holdings") or []
    cash_before = float(paper.get("cash") or 0)
    position_count_before = len(
        [h for h in holdings if float(h.get("shares") or 0) > 0]
    )
    equity_before: Optional[float] = None
    try:
        from core.paper import mark_to_market as _mtm0

        equity_before = float((_mtm0(paper) or {}).get("equity") or 0) or None
    except Exception:
        equity_before = None
    cash = cash_before
    sell_trades: List[dict] = []
    kept = []
    turnover_capped = False
    turnover_skipped: List[str] = []
    # 卖出腿（含分池膨胀减仓）可能写入 warnings；须在首次引用前初始化
    risk_gate: Dict[str, Any] = {"ok": True, "blocks": [], "warnings": []}
    force_trim_sold: set = set()
    force_trim_cut_in_book = False
    # P3-1：换手预算背包再分配 — 首轮被换手软上限跳过的候选，保留重试上下文，半仓榨干剩余预算
    _turnover_retry_pool: List[dict] = []
    # P3-3：卖出侧涨跌停/停牌跳过记录
    sell_match_skips: List[dict] = []
    # 本轮膨胀减仓砍掉的簿内票，买腿禁止立刻买回
    force_trim_no_rebuy: set = set()

    # 舆情先验：卖/买前一次性批量拉取（gate+scale_holds 时含持仓），禁止循环内 N×串行 AkShare
    prior_by_code: Dict[str, Any] = {}
    sentiment_prior_summary: Dict[str, Any] = {"ok": True, "skipped": True}
    prior_cfg_live: Dict[str, Any] = {"mode": "off"}
    try:
        from core.sentiment_prior import (
            check_sentiment_priors_for_codes,
            get_sentiment_prior_cfg,
        )

        prior_cfg_live = get_sentiment_prior_cfg()
        if skip_sentiment_prior:
            sentiment_prior_summary = {
                "ok": True,
                "skipped": True,
                "warnings": [],
                "blocks": [],
                "note": "确认落账复用预演·跳过舆情重拉",
            }
            prior_cfg_live = {**prior_cfg_live, "mode": "off", "scale_holds": False}
        elif str(prior_cfg_live.get("mode") or "off") != "off":
            hold_codes_all = [
                str(h.get("stock_code") or "").strip()
                for h in holdings
                if str(h.get("stock_code") or "").strip()
            ]
            buy_cand_codes = [
                str(x.get("stock_code") or "").strip()
                for x in top_items
                if str(x.get("stock_code") or "").strip() and not x.get("hard_reject")
            ]
            need_prior = list(buy_cand_codes)
            if prior_cfg_live.get("scale_holds") or prior_cfg_live.get("mode") == "gate":
                need_prior = list(dict.fromkeys([*hold_codes_all, *buy_cand_codes]))
            if need_prior:
                sentiment_prior_summary = check_sentiment_priors_for_codes(need_prior)
                prior_by_code = dict(sentiment_prior_summary.get("by_code") or {})
    except Exception as exc:
        logger.warning("batch sentiment prior failed: %s", exc, exc_info=True)
        sentiment_prior_summary = {"ok": True, "error": str(exc), "by_code": {}}

    # P0 · 批量预取行情（避免循环内串行网络往返）
    _sell_codes = [str(h.get("stock_code") or "") for h in holdings if h.get("stock_code")]
    _quote_cache: Dict[str, dict] = _batch_query_quotes(_sell_codes)
    event_prior_soft_holds: List[dict] = []
    # P1b：一次预取行情；广度按票用同 sector 同伴（不足回退全持仓）
    _sector_breadth_by_code: Dict[str, Optional[float]] = {}
    try:
        from core.event_prior import compute_sector_gap_breadth_live, get_event_prior_cfg

        _epcfg = get_event_prior_cfg()
        if str(_epcfg.get("mode") or "off") != "off" and _sell_codes:
            for _c in _sell_codes:
                _br = compute_sector_gap_breadth_live(
                    _sell_codes,
                    gap_trigger_pct=float(_epcfg.get("gap_trigger_pct") or 2.0),
                    quotes=_quote_cache,
                    focus_code=_c,
                    use_sector_peers=True,
                )
                _sector_breadth_by_code[_c] = _br.get("breadth")
    except Exception:
        _sector_breadth_by_code = {}

    for h in holdings:
        code = str(h.get("stock_code") or "")
        shares = float(h.get("shares") or 0)
        cost = float(h.get("cost") or 0)
        if not code or shares <= 0:
            continue

        score = score_by_code.get(code)
        in_top = code in top_codes
        reason = None
        reason_tag = None  # 结构化标识，用于 soft-hold 判断
        src_item = item_by_code.get(code) or {}
        if not respect_max_positions:
            # 分池滞回：双轨 hold —— predicted 用 ŷ_trade；heuristic 用 0–100
            if code in hard_reject_by_code:
                reason = hard_reject_by_code[code]
                reason_tag = SELL_REASON_HARD_REJECT
            else:
                try:
                    from core.signal.rebalance_tracks import hold_decision_for_item

                    should_sell, dec_sc, hold_f, track = hold_decision_for_item(
                        src_item or {"score": score, "predicted_score_blend": score},
                        tracks_cfg=_tracks_cfg,
                        predicted_hold_floor=min_hold_score,
                    )
                    if dec_sc is not None:
                        score = dec_sc
                        score_by_code[code] = dec_sc
                    if should_sell:
                        if track == "heuristic":
                            reason = (
                                f"heuristic 低于卖出门槛 min_hold({hold_f})"
                            )
                        else:
                            reason = (
                                f"ŷ_trade 低于卖出门槛 min_hold({hold_f})"
                            )
                        reason_tag = SELL_REASON_BELOW_HOLD
                except Exception:
                    if score is not None and score < min_hold_score:
                        reason = f"ŷ_trade 低于卖出门槛 min_hold({min_hold_score})"
                        reason_tag = SELL_REASON_BELOW_HOLD
        else:
            if code in hard_reject_by_code:
                reason = hard_reject_by_code[code]
                reason_tag = SELL_REASON_HARD_REJECT
            elif not in_top:
                reason = "不在横截面 TopK"
                reason_tag = SELL_REASON_NOT_IN_TOPK
            else:
                try:
                    from core.signal.rebalance_tracks import hold_decision_for_item

                    should_sell, dec_sc, hold_f, track = hold_decision_for_item(
                        src_item or {"score": score, "predicted_score_blend": score},
                        tracks_cfg=_tracks_cfg,
                        predicted_hold_floor=min_hold_score,
                    )
                    if dec_sc is not None:
                        score = dec_sc
                    if should_sell:
                        reason = (
                            f"heuristic<{hold_f}"
                            if track == "heuristic"
                            else f"ŷ_trade 低于 min_hold_score({hold_f})"
                        )
                        reason_tag = SELL_REASON_BELOW_HOLD
                except Exception:
                    if score is not None and score < min_hold_score:
                        reason = f"ŷ_trade 低于 min_hold_score({min_hold_score})"
                        reason_tag = SELL_REASON_BELOW_HOLD

        # P1：主题开盘缺口 / rem / 舆情看多 · 卖出改为 soft hold（不改 ŷ）
        # 分池滞回卖因「低于*」；横截面另有「不在 TopK」——主题日同样保护，避免踏空
        _soft_hold_eligible = bool(
            reason and reason_tag in _SOFT_HOLD_ELIGIBLE_REASONS
        )
        if _soft_hold_eligible:
            try:
                from core.event_prior import (
                    build_event_prior_from_quote,
                    get_event_prior_cfg,
                    should_soft_hold_for_low_score,
                )
                from core.sentiment_prior import should_soft_hold_from_sentiment
                from quant.research.rem_ridge import predict_rem_from_features

                ep_cfg = get_event_prior_cfg()
                rem_yhat = None
                q_ep = _quote_cache.get(code) or {}
                _sector_breadth = _sector_breadth_by_code.get(code)
                if str(ep_cfg.get("mode") or "off") != "off":
                    try:
                        from core.event_prior import gap_pct_from_quote_bars

                        gap_v = gap_pct_from_quote_bars(
                            q_ep if q_ep.get("success") else None
                        )
                        feats = {
                            "gap_pct": gap_v,
                            "open_gap": gap_v,
                            "sector_gap_breadth": _sector_breadth,
                            "theme_day": 1.0
                            if (
                                gap_v is not None
                                and float(gap_v) >= float(ep_cfg.get("gap_trigger_pct") or 2)
                            )
                            else 0.0,
                        }
                        rem_yhat = tau_by_code.get(code)
                        if rem_yhat is None:
                            rem_yhat = predict_rem_from_features(feats)
                    except Exception:
                        rem_yhat = tau_by_code.get(code)
                    ep = build_event_prior_from_quote(
                        q_ep if q_ep.get("success") else None,
                        sector_breadth=_sector_breadth,
                        rem_yhat=rem_yhat,
                        stock_code=code,
                    )
                    sent_soft = should_soft_hold_from_sentiment(
                        prior_by_code.get(code)
                    )
                    y_conflict = False
                    try:
                        from core.signal.y_state import stamp_y_state

                        # 卖出保护：双头分歧时暂缓因低分清仓
                        sig_row = {
                            "predicted_score": None,
                            "predicted_score_eod_rem": None,
                            "predicted_score_tau": rem_yhat,
                            "gap_pct": ep.get("gap_pct") if isinstance(ep, dict) else None,
                            "dual_score_window": "intraday",
                        }
                        # 尽量用持仓/评分上已有字段
                        for src in (h,):
                            if isinstance(src, dict):
                                for k in (
                                    "predicted_score",
                                    "predicted_score_eod",
                                    "predicted_score_eod_rem",
                                    "predicted_score_tau",
                                    "predicted_score_blend",
                                    "score_rem",
                                    "dual_score_head",
                                    "y_check",
                                ):
                                    if src.get(k) is not None:
                                        sig_row[k] = src.get(k)
                        if sig_row.get("y_check") is None:
                            stamp_y_state(sig_row)
                        y_conflict = str(sig_row.get("y_check") or "") == "conflict"
                    except Exception:
                        y_conflict = False
                    if should_soft_hold_for_low_score(ep) or sent_soft or y_conflict:
                        reason = None
                        kept.append(h)
                        event_prior_soft_holds.append(
                            {
                                "stock_code": code,
                                "gap_pct": ep.get("gap_pct") if isinstance(ep, dict) else None,
                                "sector_breadth": _sector_breadth,
                                "rem_yhat": rem_yhat,
                                "sentiment_soft_hold": bool(sent_soft),
                                "y_check_soft_hold": bool(y_conflict),
                                "y_check": "conflict" if y_conflict else None,
                                "warnings": list((ep or {}).get("warnings") or []),
                            }
                        )
                        continue
            except Exception:
                logger.warning("sell_loop event_prior failed for %s", code, exc_info=True)

        sell_shares = shares
        keep_shares = 0.0
        sell_note = None
        prior_trim = False
        if not reason:
            # 舆情先验：gate + scale_holds → 已持仓缩至 scale_buy_pct（不改 ŷ）
            if str(prior_cfg_live.get("mode") or "off") == "off" or not prior_cfg_live.get(
                "scale_holds"
            ):
                kept.append(h)
                continue
            try:
                from core.sentiment_prior import apply_prior_to_hold

                prior = prior_by_code.get(code)
                if not isinstance(prior, dict):
                    kept.append(h)
                    continue
                hold_apply = apply_prior_to_hold(prior, shares=shares)
                if hold_apply.get("trim") and float(hold_apply.get("sell_shares") or 0) > 0:
                    sell_shares = float(hold_apply["sell_shares"])
                    keep_shares = float(hold_apply.get("keep_shares") or 0)
                    scale_h = hold_apply.get("scale")
                    sell_note = (
                        f"舆情先验缩仓至 {float(scale_h):.0%}"
                        if scale_h is not None
                        else "舆情先验缩仓"
                    )
                    prior_trim = True
                    reason = hold_apply.get("reason") or "sentiment_prior_bearish"
                    reason_tag = SELL_REASON_SENTIMENT_TRIM
                else:
                    kept.append(h)
                    continue
            except Exception:
                logger.warning("sell_loop sentiment_prior failed for %s", code, exc_info=True)
                kept.append(h)
                continue

        quote = _quote_cache.get(code) or {}
        price = _quote_price(quote) if quote.get("success") else None
        if not price or price <= 0:
            kept.append(h)
            continue

        # P3-3：跌停/停牌无法成交 → 跳过卖出，保留持仓
        _sell_block = _sell_match_block_reason(code, quote)
        if _sell_block:
            sell_match_skips.append({
                "stock_code": code,
                "stock_name": h.get("stock_name"),
                "reason": _sell_block,
                "score": score,
                "path": "main",
            })
            kept.append(h)
            continue

        pnl_pct = round((price / cost - 1.0) * 100.0, 2) if cost else None
        fill_px = apply_fill_price(
            "sell", float(price), model=cost_model, params=fee_params
        )
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees(
            "sell", amount, model=cost_model, params=fee_params
        )
        note = (
            sell_note
            if prior_trim
            else (
                f"分池调仓卖出：{reason}"
                if not respect_max_positions
                else f"横截面调仓卖出：{reason}"
            )
        )
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": h.get("stock_name") or quote.get("stock_name"),
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "pnl_pct": pnl_pct,
                "score": score,
                "origin": ORIGIN_STRATEGY,
                "note": note,
                "sentiment_prior": bool(prior_trim),
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        sell_trades.append(trade)
        cash = round(cash + float(fee_info["net_cash_delta"]), 2)
        if prior_trim and keep_shares > 0:
            base_sh = float(h.get("sentiment_trim_base_shares") or shares)
            kept.append(
                {
                    **h,
                    "shares": keep_shares,
                    "sentiment_trim_base_shares": base_sh,
                }
            )

    paper["holdings"] = kept
    paper["cash"] = round(cash, 2)

    # 分池滞回：持仓膨胀时优先卸中间带，再卸簿内最低分；跌停则换下一可卖票
    if not respect_max_positions and len(kept) > max_positions:
        trim_count = len(kept) - max_positions
        sellable, trim_blocked = select_force_trim_codes_sellable(
            kept,
            score_by_code=score_by_code,
            top_codes=top_codes,
            trim_count=trim_count,
            quote_cache=_quote_cache,
            sell_block_fn=_sell_match_block_reason,
        )
        for b in trim_blocked:
            sell_match_skips.append(
                {
                    "stock_code": b.get("stock_code"),
                    "stock_name": next(
                        (
                            h.get("stock_name")
                            for h in kept
                            if str(h.get("stock_code") or "") == b.get("stock_code")
                        ),
                        None,
                    ),
                    "reason": b.get("reason"),
                    "score": b.get("score"),
                    "path": "force_trim",
                }
            )
        if len(sellable) < trim_count:
            warns = list(risk_gate.get("warnings") or [])
            msg = (
                f"分池膨胀减仓：需卸 {trim_count} 只，可卖 {len(sellable)} 只"
                f"（{len(trim_blocked)} 只跌停/停牌跳过）"
            )
            if msg not in warns:
                warns.append(msg)
            risk_gate["warnings"] = warns
            risk_gate["force_trim_incomplete"] = True
        force_sell_codes = set(sellable)
        new_kept = []
        force_sell_trades: List[dict] = []
        for h in kept:
            code = str(h.get("stock_code") or "")
            if code in force_sell_codes:
                shares = float(h.get("shares") or 0)
                cost = float(h.get("cost") or 0)
                if shares <= 0:
                    new_kept.append(h)
                    continue
                quote = _quote_cache.get(code) or {}
                price = _quote_price(quote) if quote.get("success") else None
                if not price or price <= 0:
                    new_kept.append(h)
                    continue
                band = "中间带" if code not in top_codes else "簿内"
                if code in top_codes:
                    force_trim_no_rebuy.add(code)
                pnl_pct = round((price / cost - 1.0) * 100.0, 2) if cost else None
                fill_px = apply_fill_price("sell", float(price), model=cost_model, params=fee_params)
                amount = round(shares * fill_px, 2)
                fee_info = calc_trade_fees("sell", amount, model=cost_model, params=fee_params)
                trade = annotate_trade(
                    {
                        "ts": _now_iso(),
                        "side": "sell",
                        "stock_code": code,
                        "stock_name": h.get("stock_name"),
                        "shares": shares,
                        "price": round(fill_px, 4),
                        "amount": amount,
                        "pnl_pct": pnl_pct,
                        "score": score_by_code.get(code),
                        "origin": ORIGIN_STRATEGY,
                        "note": f"分池持仓膨胀强制减仓·{band}（持仓 {len(kept)} > 簿长 {max_positions}）",
                    },
                    fee_info,
                )
                paper.setdefault("trades", []).append(trade)
                sell_trades.append(trade)
                force_sell_trades.append(trade)
                cash = round(cash + float(fee_info["net_cash_delta"]), 2)
            else:
                new_kept.append(h)
        kept = new_kept
        paper["holdings"] = kept
        paper["cash"] = round(cash, 2)
        force_trim_sold = {
            str(t.get("stock_code") or "") for t in force_sell_trades if t.get("stock_code")
        }
        force_trim_cut_in_book = any(c in top_codes for c in force_trim_sold)
        if force_sell_trades:
            if force_trim_no_rebuy:
                warns = list(risk_gate.get("warnings") or [])
                msg = (
                    "膨胀减仓已卸簿内 "
                    + ",".join(sorted(force_trim_no_rebuy))
                    + "，本轮不买回"
                )
                if msg not in warns:
                    warns.append(msg)
                risk_gate["warnings"] = warns
            append_operation_log(
                paper,
                "force_trim",
                detail=f"分池持仓膨胀强制减仓 {len(force_sell_trades)} 只至簿长 {max_positions}",
                meta={
                    "trim_count": len(force_sell_trades),
                    "max_positions": max_positions,
                    "codes": [str(t.get("stock_code")) for t in force_sell_trades],
                    "prefer_mid_band": True,
                    "cut_in_book": force_trim_cut_in_book,
                    "path": "cluster",
                },
            )
            if force_trim_cut_in_book:
                warns = list(risk_gate.get("warnings") or [])
                msg = "膨胀减仓卸了簿内票（中间带不可卖）：本轮不买回，避免空转"
                if msg not in warns:
                    warns.append(msg)
                risk_gate["warnings"] = warns

    buy_trades: List[dict] = []
    sentiment_restore_trades: List[dict] = []
    held_codes = {str(h.get("stock_code")) for h in kept}
    holdings = kept
    buys_blocked = False
    risk_budget_skips: List[dict] = []
    mid_summary: Dict[str, Any] = {}
    risk_limits: Dict[str, Any] = {}
    _tau_floor_meta: Dict[str, Any] = {}
    _tau_floor: Optional[float] = None
    # 膨胀减仓可能已写入 warnings / force_trim_incomplete，账户风控覆盖后并回
    _trim_warns = list(risk_gate.get("warnings") or [])
    _trim_incomplete = bool(risk_gate.get("force_trim_incomplete"))

    # 卖出后、买入前：账户风控；回撤硬拦，单票/行业改走逐笔预算缩量（P1）
    try:
        from core.paper import mark_to_market
        from core.risk import check_account_risk

        mid_summary = mark_to_market(paper)
        risk_gate = check_account_risk(paper, mid_summary)
        risk_limits = dict(risk_gate.get("limits") or {})
    except Exception:
        risk_gate = {"ok": True, "blocks": [], "warnings": []}
        risk_limits = {}
    if _trim_warns:
        warns = list(risk_gate.get("warnings") or [])
        for w in _trim_warns:
            if w not in warns:
                warns.append(w)
        risk_gate["warnings"] = warns
    if _trim_incomplete:
        risk_gate["force_trim_incomplete"] = True

    # 风控超额主动减仓：单票/行业超限 → 部分卖出至限额内（不只拦买入）
    risk_excess_trims: List[dict] = []
    if mid_summary and risk_limits:
        _eq = float(mid_summary.get("equity") or 0) or equity_before or 0
        _max_pos_pct = float(risk_limits.get("max_position_pct") or 25.0)
        _max_sec_pct = float(risk_limits.get("max_sector_pct") or 40.0)
        if _eq > 0:
            from core.portfolio_optimize import _sector_for, load_sector_map as _lsm
            _smap = _lsm()
            # 单票超限减仓
            for h in list(kept):
                code = str(h.get("stock_code") or "")
                shares = float(h.get("shares") or 0)
                if shares <= 0:
                    continue
                mv = float(h.get("market_value") or 0)
                if mv <= 0:
                    cost = float(h.get("cost") or 0)
                    mv = cost * shares
                pct = mv / _eq * 100.0 if _eq > 0 else 0
                if pct > _max_pos_pct:
                    # 减仓到限额以下
                    target_mv = _eq * _max_pos_pct / 100.0
                    trim_shares = int((mv - target_mv) / (mv / shares) // 100) * 100
                    if trim_shares >= 100:
                        quote = _quote_cache.get(code) or {}
                        price = _quote_price(quote) if quote.get("success") else None
                        if price and price > 0:
                            # P3-3：跌停/停牌 → 跳过风控减仓
                            _sell_block = _sell_match_block_reason(code, quote)
                            if _sell_block:
                                sell_match_skips.append({
                                    "stock_code": code,
                                    "stock_name": h.get("stock_name"),
                                    "reason": _sell_block,
                                    "score": score_by_code.get(code),
                                    "path": "risk_excess_name",
                                })
                                continue
                            fill_px = apply_fill_price("sell", float(price), model=cost_model, params=fee_params)
                            amount = round(trim_shares * fill_px, 2)
                            fee_info = calc_trade_fees("sell", amount, model=cost_model, params=fee_params)
                            trade = annotate_trade(
                                {
                                    "ts": _now_iso(),
                                    "side": "sell",
                                    "stock_code": code,
                                    "stock_name": h.get("stock_name"),
                                    "shares": trim_shares,
                                    "price": round(fill_px, 4),
                                    "amount": amount,
                                    "score": score_by_code.get(code),
                                    "origin": ORIGIN_STRATEGY,
                                    "note": f"风控超额减仓：仓位 {pct:.1f}% > 单票上限 {_max_pos_pct}%",
                                },
                                fee_info,
                            )
                            paper.setdefault("trades", []).append(trade)
                            sell_trades.append(trade)
                            risk_excess_trims.append(trade)
                            cash = round(cash + float(fee_info["net_cash_delta"]), 2)
                            # 更新持仓（shares + market_value 同步，避免后续行业减仓读旧市值导致过度减仓）
                            before_shares = float(shares or 0)
                            after_shares = before_shares - float(trim_shares or 0)
                            h["shares"] = after_shares
                            if after_shares <= 0:
                                kept.remove(h)
                            else:
                                h["market_value"] = round(after_shares * float(fill_px), 2)

            # 行业超限：行业内按 ŷ 从低到高部分卖出，直至落入限额
            for _round in range(8):
                sec_mv: Dict[str, float] = {}
                sec_holds: Dict[str, List[dict]] = {}
                for h in kept:
                    code = str(h.get("stock_code") or "")
                    sh = float(h.get("shares") or 0)
                    if sh <= 0:
                        continue
                    sec = str(h.get("sector") or _sector_for(code, _smap) or "其他")
                    h["sector"] = sec
                    mv = float(h.get("market_value") or 0)
                    if mv <= 0:
                        mv = float(h.get("cost") or 0) * sh
                    sec_mv[sec] = float(sec_mv.get(sec) or 0.0) + mv
                    sec_holds.setdefault(sec, []).append(h)
                progressed = False
                for sec, total_mv in list(sec_mv.items()):
                    sec_pct = total_mv / _eq * 100.0 if _eq > 0 else 0.0
                    if sec_pct <= _max_sec_pct + 1e-9:
                        continue
                    need_cut = total_mv - (_eq * _max_sec_pct / 100.0)
                    ranked = sorted(
                        sec_holds.get(sec) or [],
                        key=lambda hh: (
                            float(score_by_code[str(hh.get("stock_code") or "")])
                            if score_by_code.get(str(hh.get("stock_code") or "")) is not None
                            else float("-inf")
                        ),
                    )
                    for h in ranked:
                        if need_cut <= 0:
                            break
                        code = str(h.get("stock_code") or "")
                        shares = float(h.get("shares") or 0)
                        mv = float(h.get("market_value") or 0)
                        if mv <= 0:
                            mv = float(h.get("cost") or 0) * shares
                        if shares < 100 or mv <= 0:
                            continue
                        px = mv / shares
                        trim_shares = int(min(need_cut, mv) / px // 100) * 100
                        if trim_shares < 100:
                            continue
                        quote = _quote_cache.get(code) or {}
                        price = _quote_price(quote) if quote.get("success") else None
                        if not price or price <= 0:
                            continue
                        _sell_block = _sell_match_block_reason(code, quote)
                        if _sell_block:
                            sell_match_skips.append({
                                "stock_code": code,
                                "stock_name": h.get("stock_name"),
                                "reason": _sell_block,
                                "score": score_by_code.get(code),
                                "path": "risk_excess_sector",
                            })
                            continue
                        fill_px = apply_fill_price(
                            "sell", float(price), model=cost_model, params=fee_params
                        )
                        amount = round(trim_shares * fill_px, 2)
                        fee_info = calc_trade_fees(
                            "sell", amount, model=cost_model, params=fee_params
                        )
                        trade = annotate_trade(
                            {
                                "ts": _now_iso(),
                                "side": "sell",
                                "stock_code": code,
                                "stock_name": h.get("stock_name"),
                                "shares": trim_shares,
                                "price": round(fill_px, 4),
                                "amount": amount,
                                "score": score_by_code.get(code),
                                "origin": ORIGIN_STRATEGY,
                                "note": (
                                    f"风控超额减仓：行业 {sec} {sec_pct:.1f}% "
                                    f"> 上限 {_max_sec_pct}%"
                                ),
                            },
                            fee_info,
                        )
                        paper.setdefault("trades", []).append(trade)
                        sell_trades.append(trade)
                        risk_excess_trims.append(trade)
                        cash = round(cash + float(fee_info["net_cash_delta"]), 2)
                        before_mv = mv
                        h["shares"] = shares - trim_shares
                        h["market_value"] = round(float(h["shares"]) * float(fill_px), 2)
                        if h["shares"] <= 0:
                            kept.remove(h)
                            sold_mv = before_mv
                        else:
                            sold_mv = before_mv - float(h.get("market_value") or 0)
                        need_cut -= max(0.0, sold_mv)
                        progressed = True
                if not progressed:
                    break

            paper["holdings"] = kept
            paper["cash"] = round(cash, 2)
            if risk_excess_trims:
                append_operation_log(
                    paper,
                    "risk_excess_trim",
                    detail=f"风控超额主动减仓 {len(risk_excess_trims)} 笔",
                    meta={
                        "trims": [
                            {"code": t.get("stock_code"), "shares": t.get("shares"), "note": t.get("note")}
                            for t in risk_excess_trims
                        ],
                        "path": "cluster" if not respect_max_positions else "cross_section",
                    },
                )

    # 风控减仓后同步 held 视图（避免后续买入腿用过期集合）
    holdings = kept
    held_codes = {str(h.get("stock_code")) for h in kept}
    paper["holdings"] = kept
    paper["cash"] = round(cash, 2)

    # 舆情先验预检摘要：复用开环批量结果（勿二次拉取）
    try:
        held_now = {str(h.get("stock_code")) for h in kept}
        cand_codes = [
            str(x.get("stock_code") or "").strip()
            for x in top_items
            if str(x.get("stock_code") or "").strip()
            and str(x.get("stock_code") or "").strip() not in held_now
            and not x.get("hard_reject")
        ]
        if str(prior_cfg_live.get("mode") or "off") == "off":
            sentiment_prior_summary = {
                "ok": True,
                "skipped": True,
                "warnings": [],
                "blocks": [],
                "note": "舆情先验关闭",
            }
        elif not prior_by_code and not cand_codes:
            sentiment_prior_summary = {
                "ok": True,
                "warnings": [],
                "blocks": [],
                "note": "无新开仓候选",
            }
        for w in sentiment_prior_summary.get("warnings") or []:
            warns = list(risk_gate.get("warnings") or [])
            if w and w not in warns:
                warns.append(w)
            risk_gate["warnings"] = warns
        for bi in sentiment_prior_summary.get("block_items") or []:
            msg = str(bi.get("message") or "")
            if msg and msg not in (risk_gate.get("warnings") or []):
                risk_gate.setdefault("warnings", []).append(msg)
        risk_gate["sentiment_prior"] = {
            "ok": sentiment_prior_summary.get("ok"),
            "warnings": sentiment_prior_summary.get("warnings") or [],
            "blocks": sentiment_prior_summary.get("blocks") or [],
            "candidate_count": len(cand_codes),
            "hold_check_count": len(held_now)
            if prior_cfg_live.get("scale_holds")
            else 0,
        }
    except Exception as exc:
        sentiment_prior_summary = {"ok": True, "error": str(exc)}

    block_items = list((risk_gate or {}).get("block_items") or [])
    drawdown_blocks = [i for i in block_items if i.get("code") == "drawdown_limit"]
    soft_blocks = [i for i in block_items if i.get("code") != "drawdown_limit"]

    # 回撤恢复检测：current_drawdown_pct 已低于 target_dd → 解除加仓封锁
    recovery_info = (risk_gate or {}).get("recovery")
    if recovery_info and recovery_info.get("recovered") and not drawdown_blocks:
        prev_blocked = bool(paper.get("drawdown_blocked_since"))
        if prev_blocked:
            paper.pop("drawdown_blocked_since", None)
            append_operation_log(
                paper,
                "risk_recovery",
                detail=str(recovery_info.get("note") or "回撤恢复，加仓解锁"),
                meta={
                    "current_dd": recovery_info.get("current_dd"),
                    "recovery_threshold": recovery_info.get("recovery_threshold"),
                    "path": "cluster" if not respect_max_positions else "cross_section",
                },
            )

    if drawdown_blocks:
        buys_blocked = True
        paper["drawdown_blocked_since"] = _now_iso()
        blocks = [str(i.get("message") or i.get("code")) for i in drawdown_blocks]
        append_operation_log(
            paper,
            "risk_block",
            detail="横截面调仓风控拦截加仓："
            + ("；".join(blocks) or "回撤超限"),
            meta={
                "codes": ["drawdown_limit"],
                "block_items": drawdown_blocks,
                "blocks": blocks,
                "warnings": risk_gate.get("warnings") or [],
                "limits": risk_limits,
                "recovery": recovery_info,
                "path": "cluster" if not respect_max_positions else "cross_section",
            },
        )
    if soft_blocks and not drawdown_blocks:
        # 已有超限：不整批拦买，逐笔缩量；文案进 warnings（勿再标 blocks 以免 UI 显示整批拦截）
        soft_msgs = [
            str(i.get("message") or i.get("code")) for i in soft_blocks if i
        ]
        warns = list(risk_gate.get("warnings") or [])
        for m in soft_msgs:
            tip = f"已超限·买入缩量：{m}" if m else m
            if tip and tip not in warns:
                warns.append(tip)
        risk_gate["warnings"] = warns
        risk_gate["ok"] = True
        risk_gate["blocks"] = []
        risk_gate["block_items"] = []
        risk_gate["soft_block_items"] = soft_blocks
        append_operation_log(
            paper,
            "risk_budget",
            detail="持仓已触单票/行业上限 · 买入按剩余预算缩量："
            + ("；".join(soft_msgs[:4]) or "见 limits"),
            meta={
                "block_items": soft_blocks,
                "limits": risk_limits,
                "path": "cluster" if not respect_max_positions else "cross_section",
            },
        )

    # 目标权重建议 + 逐笔风险预算（restore 在回撤硬拦时仍执行）
    target_w: Dict[str, Any] = {}
    try:
        from core.portfolio_optimize import optimize_weights
        from core.strategy import get_strategy_spec

        sid = paper.get("strategy_id") or "short"
        rr = (get_strategy_spec(str(sid)).get("risk") or {})
        if not risk_limits:
            risk_limits = {
                "max_position_pct": float(rr.get("max_position_pct") or 25.0),
                "max_sector_pct": float(rr.get("max_sector_pct") or 40.0),
            }
        opt_cap = (
            max_positions
            if not respect_max_positions
            else int(rr.get("max_positions") or max_positions)
        )
        opt_min = float(min_score)
        weight_mode = str(
            rules.get("weight_mode")
            or rr.get("weight_mode")
            or "score_budget"
        ).strip() or "score_budget"
        opt = optimize_weights(
            list(ranking or []),
            max_position_pct=float(
                risk_limits.get("max_position_pct")
                or rr.get("max_position_pct")
                or 25.0
            ),
            max_sector_pct=float(
                risk_limits.get("max_sector_pct")
                or rr.get("max_sector_pct")
                or 40.0
            ),
            max_positions=opt_cap,
            min_score=opt_min,
            weight_mode=weight_mode,
        )
        paper["last_optimize"] = opt
        target_w = opt.get("weights_pct") or {}
    except Exception as e:
        paper["last_optimize"] = None
        target_w = {}
        if not risk_limits:
            risk_limits = {"max_position_pct": 25.0, "max_sector_pct": 40.0}
        warn = f"optimize_weights 失败，已降级无目标仓约束: {type(e).__name__}: {e}"
        warns = list(risk_gate.get("warnings") or [])
        if warn not in warns:
            warns.append(warn)
        risk_gate["warnings"] = warns
        risk_gate["optimize_fallback"] = True

    from core.portfolio_optimize import _sector_for, load_sector_map
    from core.risk.budget import (
        build_running_exposure_mv,
        clip_buy_to_risk_budget,
    )

    smap = load_sector_map()
    mid_holdings = list((mid_summary or {}).get("holdings") or holdings)
    name_mv, sector_mv = build_running_exposure_mv(mid_holdings, sector_map=smap)
    budget_equity = float(
        (mid_summary or {}).get("equity") or equity_before or 0
    ) or float(equity_before or 0)
    max_pos_pct = float(risk_limits.get("max_position_pct") or 25.0)
    max_sec_pct = float(risk_limits.get("max_sector_pct") or 40.0)

    # P0 · 批量预取买入候选行情（含舆情缩仓待补回）
    _restore_codes = [
        str(h.get("stock_code") or "")
        for h in holdings
        if h.get("sentiment_trim_base_shares") is not None
        and str(h.get("stock_code") or "").strip()
    ]
    _buy_codes = [
        str(it.get("stock_code") or "")
        for it in top_items
        if it.get("stock_code") and not it.get("hard_reject")
    ]
    _buy_quote_cache: Dict[str, dict] = _batch_query_quotes(
        list(dict.fromkeys([*_restore_codes, *_buy_codes]))
    )

    # 舆情缩仓 restore：先验不再要求 scale_hold 时，补回至 sentiment_trim_base_shares
    if (
        not skip_sentiment_prior
        and str(prior_cfg_live.get("mode") or "off") != "off"
    ):
        try:
            from core.sentiment_prior import apply_prior_restore_hold

            for hi, h in enumerate(list(holdings)):
                code = str(h.get("stock_code") or "").strip()
                if not code:
                    continue
                base_sh = h.get("sentiment_trim_base_shares")
                if base_sh is None:
                    continue
                cur_sh = float(h.get("shares") or 0)
                restore = apply_prior_restore_hold(
                    prior_by_code.get(code),
                    current_shares=cur_sh,
                    base_shares=base_sh,
                )
                if restore.get("clear_base") and not restore.get("restore"):
                    h2 = {**h}
                    h2.pop("sentiment_trim_base_shares", None)
                    holdings[hi] = h2
                    continue
                if not restore.get("restore"):
                    continue
                buy_sh = float(restore.get("buy_shares") or 0)
                if buy_sh < 100:
                    continue
                quote = _buy_quote_cache.get(code) or {}
                price = _quote_price(quote)
                if not price or price <= 0:
                    continue
                skip_match = _buy_match_block_reason(code, quote)
                if skip_match:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": h.get("stock_name"),
                            "reason": f"舆情补回跳过：{skip_match}",
                            "sentiment_restore": True,
                        }
                    )
                    continue
                sector = str(
                    h.get("sector") or _sector_for(code, smap) or "其他"
                )
                clip = clip_buy_to_risk_budget(
                    code=code,
                    sector=sector,
                    price=float(price),
                    shares=int(buy_sh),
                    equity=budget_equity,
                    name_mv=name_mv,
                    sector_mv=sector_mv,
                    max_position_pct=max_pos_pct,
                    max_sector_pct=max_sec_pct,
                )
                if clip.get("skipped"):
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": h.get("stock_name"),
                            "reason": clip.get("reason") or "舆情补回·风险预算跳过",
                            "sentiment_restore": True,
                        }
                    )
                    continue
                shares = int(clip.get("shares") or buy_sh)
                if shares < 100:
                    continue
                fill_px = apply_fill_price(
                    "buy", float(price), model=cost_model, params=fee_params
                )
                amount = round(shares * fill_px, 2)
                if (
                    max_turnover_pct is not None
                    and equity_before
                    and equity_before > 0
                ):
                    sell_amt, buy_amt_so_far, buy_budget_amt = (
                        resolve_buy_turnover_budget(
                            sell_trades=sell_trades,
                            buy_trades=buy_trades,
                            equity_before=equity_before,
                            max_turnover_pct=float(max_turnover_pct),
                        )
                    )
                    clipped_sh = clip_shares_to_turnover_budget(
                        shares=shares,
                        fill_px=fill_px,
                        buy_amt_so_far=buy_amt_so_far,
                        buy_budget_amt=buy_budget_amt,
                        sell_amt=sell_amt,
                        equity_before=equity_before,
                        max_turnover_pct=float(max_turnover_pct),
                    )
                    if clipped_sh < 100:
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": h.get("stock_name"),
                                "reason": "舆情补回跳过：换手预算不足",
                                "sentiment_restore": True,
                            }
                        )
                        turnover_capped = True
                        continue
                    if clipped_sh < shares:
                        shares = clipped_sh
                        amount = round(shares * fill_px, 2)
                        turnover_capped = True
                fee_info = calc_trade_fees(
                    "buy", amount, model=cost_model, params=fee_params
                )
                need = amount + float(fee_info.get("fees") or 0)
                if need > cash + 1e-6:
                    continue
                trade = annotate_trade(
                    {
                        "ts": _now_iso(),
                        "side": "buy",
                        "stock_code": code,
                        "stock_name": h.get("stock_name")
                        or quote.get("stock_name"),
                        "shares": shares,
                        "price": round(fill_px, 4),
                        "amount": amount,
                        "score": score_by_code.get(code),
                        "origin": ORIGIN_STRATEGY,
                        "note": "舆情先验恢复补仓",
                        "sentiment_restore": True,
                        "sector": sector,
                    },
                    fee_info,
                )
                paper.setdefault("trades", []).append(trade)
                buy_trades.append(trade)
                sentiment_restore_trades.append(trade)
                new_shares = cur_sh + shares
                h2 = {
                    **h,
                    "shares": new_shares,
                    "market_value": round(new_shares * fill_px, 2),
                }
                if restore.get("clear_base") or new_shares + 1e-9 >= float(
                    base_sh
                ):
                    h2.pop("sentiment_trim_base_shares", None)
                holdings[hi] = h2
                cash = round(cash + float(fee_info["net_cash_delta"]), 2)
                name_mv[code] = float(name_mv.get(code) or 0.0) + amount
                sector_mv[sector] = float(sector_mv.get(sector) or 0.0) + amount
            paper["holdings"] = holdings
            paper["cash"] = round(cash, 2)
            if sentiment_restore_trades:
                append_operation_log(
                    paper,
                    "sentiment_restore",
                    detail=f"舆情缩仓恢复补仓 {len(sentiment_restore_trades)} 笔",
                    meta={
                        "codes": [
                            str(t.get("stock_code"))
                            for t in sentiment_restore_trades
                        ],
                        "path": "cluster"
                        if not respect_max_positions
                        else "cross_section",
                    },
                )
        except Exception as exc:
            logger.warning("sentiment restore failed: %s", exc, exc_info=True)

    if not drawdown_blocks:
        # τ 买入门槛：候选池无人过基线时冻结降级（可回滚）
        try:
            from core.signal.dual_score import resolve_tau_buy_floor_for_pool
            from core.signal.rebalance_tracks import should_apply_tau_gate

            _tau_pool = [
                it
                for it in top_items
                if isinstance(it, dict)
                and str(it.get("stock_code") or "") not in held_codes
                and not it.get("hard_reject")
                and should_apply_tau_gate(it, tracks_cfg=_tracks_cfg)
            ]
            _tau_floor, _tau_floor_meta = resolve_tau_buy_floor_for_pool(_tau_pool)
            if str(_tau_floor_meta.get("mode") or "") == "freeze_breakglass":
                note = str(_tau_floor_meta.get("note") or "τ 试验档：买入闸临时放宽")
                warns = list(risk_gate.get("warnings") or [])
                if note not in warns:
                    warns.append(note)
                    risk_gate["warnings"] = warns
        except Exception:
            logger.warning("resolve_tau_buy_floor_for_pool failed", exc_info=True)
            _tau_floor = None
            _tau_floor_meta = {}

        for item in top_items:
            # 分池滞回：账户可暂时多于簿长（中间带未清仓）；买入上限只看「已持目标簿只数」
            if not respect_max_positions:
                book_held = sum(
                    1
                    for h in holdings
                    if str(h.get("stock_code") or "") in top_codes
                )
                if book_held >= max_positions:
                    break
            elif len(holdings) >= max_positions:
                break
            code = str(item.get("stock_code") or "")
            if not code or code in held_codes:
                continue
            if code in force_trim_no_rebuy:
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": "force_trim_no_rebuy",
                        "score": item.get("score"),
                    }
                )
                continue
            if item.get("hard_reject"):
                continue
            # SS-E2：predicted 轨须 production ŷ；heuristic 轨仍走 buy_gate_for_item
            try:
                from core.signal.gate import allows_production_yhat
                from core.signal.rebalance_tracks import (
                    TRACK_HEURISTIC,
                    resolve_score_track,
                )

                if resolve_score_track(item) != TRACK_HEURISTIC:
                    poke, poke_reason = allows_production_yhat(item)
                    if not poke:
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": item.get("stock_name"),
                                "reason": poke_reason or "production_yhat_gate",
                                "score": item.get("score"),
                                "production_ok": False,
                                "score_track": item.get("score_track"),
                            }
                        )
                        continue
            except Exception:
                pass
            score = item.get("score")
            # 买入门槛：双轨（ŷ_EOD% 或 heuristic 0–100）
            try:
                from core.signal.rebalance_tracks import (
                    buy_gate_for_item,
                    should_apply_tau_gate,
                )

                ok_buy, gate_sc, _track, skip_r = buy_gate_for_item(
                    item,
                    tracks_cfg=_tracks_cfg,
                    predicted_buy_floor=min_score,
                )
                if not ok_buy:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": item.get("stock_name"),
                            "reason": skip_r or "below_eod_floor",
                            "score": score,
                            "eod_gate_score": gate_sc,
                            "min_score": min_score,
                            "score_track": item.get("score_track"),
                        }
                    )
                    continue
            except Exception:
                try:
                    from core.signal.dual_score import eod_gate_score_for_item

                    gate_sc = eod_gate_score_for_item(item)
                    if gate_sc is None and code in eod_score_by_code:
                        gate_sc = eod_score_by_code.get(code)
                except Exception:
                    gate_sc = eod_score_by_code.get(code)
                if gate_sc is None or float(gate_sc) < min_score:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": item.get("stock_name"),
                            "reason": "below_eod_floor",
                            "score": score,
                            "eod_gate_score": gate_sc,
                            "min_score": min_score,
                        }
                    )
                    continue

            # F1：ŷ_τ 买入闸（heuristic 袖仓默认跳过）
            try:
                from core.signal.rebalance_tracks import should_apply_tau_gate
                from core.signal.dual_score import buy_passes_tau_gate

                if should_apply_tau_gate(item, tracks_cfg=_tracks_cfg):
                    tau_ok, tau_reason = buy_passes_tau_gate(
                        item, floor=_tau_floor
                    )
                    if not tau_ok:
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": item.get("stock_name"),
                                "reason": tau_reason or "ŷ_τ 买入闸",
                                "score": score,
                                "eod_gate_score": gate_sc,
                                "predicted_score_tau": item.get(
                                    "predicted_score_tau", item.get("score_rem")
                                ),
                                "dual_score_tau_gate": True,
                                "tau_floor_effective": _tau_floor,
                                "tau_gate_mode": _tau_floor_meta.get("mode"),
                                "score_track": item.get("score_track"),
                            }
                        )
                        continue
            except Exception:
                logger.warning("buy_loop tau_gate failed for %s", code, exc_info=True)

            # Y(τ) 校验：双头分歧 / 缺 τ → 不按今日 EOD 新开
            try:
                from core.signal.y_state import buy_passes_y_check, stamp_y_state

                if item.get("y_check") is None:
                    stamp_y_state(item)
                y_ok, y_reason = buy_passes_y_check(item)
                if not y_ok:
                    risk_budget_skips.append(
                        {
                            "stock_code": code,
                            "stock_name": item.get("stock_name"),
                            "reason": y_reason or "Y 校验未过",
                            "score": score,
                            "eod_gate_score": gate_sc,
                            "y_check": item.get("y_check"),
                            "y_disagree": item.get("y_disagree"),
                            "eod_trust": item.get("eod_trust"),
                            "y_state_gate": True,
                        }
                    )
                    continue
            except Exception:
                logger.warning("buy_loop y_check failed for %s", code, exc_info=True)

            # 舆情先验（ŷ 外）：不改 score；gate 时 skip / 缩 ratio（用开环批量结果）
            prior_apply = None
            try:
                from core.sentiment_prior import apply_prior_to_buy

                if str(prior_cfg_live.get("mode") or "off") == "off":
                    prior_pack = None
                elif isinstance(item.get("sentiment_prior"), dict) and item.get(
                    "sentiment_prior"
                ).get("success"):
                    prior_pack = item["sentiment_prior"]
                elif code in prior_by_code:
                    prior_pack = prior_by_code[code]
                else:
                    prior_pack = None
                if prior_pack is not None:
                    prior_apply = apply_prior_to_buy(
                        prior_pack, position_ratio=position_pct
                    )
                    for w in prior_apply.get("warnings") or []:
                        warns = list(risk_gate.get("warnings") or [])
                        if w and w not in warns:
                            warns.append(w)
                            risk_gate["warnings"] = warns
                    if prior_apply.get("skip"):
                        risk_budget_skips.append(
                            {
                                "stock_code": code,
                                "stock_name": item.get("stock_name"),
                                "reason": prior_apply.get("reason")
                                or "sentiment_prior_bearish",
                                "score": score,
                                "sentiment_prior": True,
                            }
                        )
                        continue
            except Exception:
                logger.warning("buy_loop sentiment_prior failed for %s", code, exc_info=True)
                prior_apply = None

            # 横截面：optimize 未分配则跳过；分池：合并簿即目标集，不因 optimize 漏配而整票跳过
            if (
                respect_max_positions
                and target_w
                and code not in target_w
            ):
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": "不在风险预算目标仓（optimize 未分配权重）",
                        "score": score,
                    }
                )
                continue

            quote = _buy_quote_cache.get(code) or {}
            price = _quote_price(quote)
            if not price or price <= 0:
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": "无有效报价，跳过买入",
                        "score": score,
                    }
                )
                continue

            # Y2.2：涨跌停 / 停牌提示 — 最小纪律
            skip_match = _buy_match_block_reason(code, quote)
            if skip_match:
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": skip_match,
                        "score": score,
                    }
                )
                continue

            ratio = position_pct
            if prior_apply and prior_apply.get("ratio") is not None:
                try:
                    ratio = min(ratio, float(prior_apply["ratio"]))
                except (TypeError, ValueError):
                    pass
            # 目标仓：横截面硬约束缩量；分池同样吃 optimize（等权回退 1/簿长）
            if code in target_w:
                try:
                    ratio = min(ratio, float(target_w[code]) / 100.0)
                except (TypeError, ValueError):
                    pass
            elif not respect_max_positions and max_positions > 0:
                ratio = min(ratio, 1.0 / float(max_positions))
            # sizing 基准统一按净值比例：position_pct / 目标仓语义是 equity 百分比
            # 横截面过去用 cash*ratio 导致严重欠仓（满仓时单笔只有分池的 1/7），改为与分池一致
            if budget_equity > 0:
                budget = min(cash, budget_equity * ratio)
            else:
                budget = cash * ratio
            if budget < price * 100:
                continue
            shares = int(budget // price // 100) * 100
            if shares <= 0:
                continue

            sector = str(
                item.get("sector") or _sector_for(code, smap) or "其他"
            )
            clip = clip_buy_to_risk_budget(
                code=code,
                sector=sector,
                price=float(price),
                shares=shares,
                equity=budget_equity,
                name_mv=name_mv,
                sector_mv=sector_mv,
                max_position_pct=max_pos_pct,
                max_sector_pct=max_sec_pct,
            )
            if clip.get("skipped"):
                risk_budget_skips.append(
                    {
                        "stock_code": code,
                        "stock_name": item.get("stock_name"),
                        "reason": clip.get("reason") or "风险预算跳过",
                        "score": score,
                        "sector": sector,
                    }
                )
                continue
            shares = int(clip.get("shares") or shares)

            fill_px = apply_fill_price(
                "buy", float(price), model=cost_model, params=fee_params
            )
            amount = round(shares * fill_px, 2)
            fee_info = calc_trade_fees(
                "buy", amount, model=cost_model, params=fee_params
            )
            need = amount + float(fee_info.get("fees") or 0)
            if need > cash + 1e-6:
                continue

            # P0 + R5：双边换手预算拆分。50% 给 sell，50% 给 buy；sell 侧未满可溢出给 buy
            # 超限时先按剩余预算裁剪手数；仍不足 1 手再进半仓重试池
            _to_clipped = False
            if max_turnover_pct is not None and equity_before and equity_before > 0:
                _max_to = float(max_turnover_pct)
                sell_amt, buy_amt_so_far, buy_budget_amt = resolve_buy_turnover_budget(
                    sell_trades=sell_trades,
                    buy_trades=buy_trades,
                    equity_before=equity_before,
                    max_turnover_pct=_max_to,
                )
                buy_amt_after = buy_amt_so_far + amount
                proj_to = (sell_amt + buy_amt_after) / 2.0 / equity_before * 100.0
                buy_over = buy_amt_after > buy_budget_amt + 1e-9
                total_over = proj_to > _max_to + 1e-9
                if buy_over or total_over:
                    clipped_sh = clip_shares_to_turnover_budget(
                        shares=shares,
                        fill_px=fill_px,
                        buy_amt_so_far=buy_amt_so_far,
                        buy_budget_amt=buy_budget_amt,
                        sell_amt=sell_amt,
                        equity_before=equity_before,
                        max_turnover_pct=_max_to,
                    )
                    if clipped_sh >= 100:
                        shares = clipped_sh
                        amount = round(shares * fill_px, 2)
                        fee_info = calc_trade_fees(
                            "buy", amount, model=cost_model, params=fee_params
                        )
                        need = amount + float(fee_info.get("fees") or 0)
                        if need > cash + 1e-6:
                            continue
                        _to_clipped = True
                        turnover_capped = True
                    else:
                        turnover_capped = True
                        turnover_skipped.append(code)
                        _turnover_retry_pool.append({
                            "item": item,
                            "code": code,
                            "score": score,
                            "price": price,
                            "quote": quote,
                            "sector": sector,
                            "base_ratio": ratio,
                            "prior_apply_ratio": (prior_apply or {}).get("ratio"),
                            "target_w_pct": target_w.get(code),
                        })
                        continue

            tw_note = ""
            if code in target_w:
                tw_note = f" · 目标仓{float(target_w[code]):.1f}%"
            clip_note = f" · {clip['reason']}" if clip.get("clipped") else ""
            to_note = " · 换手预算裁剪" if _to_clipped else ""
            buy_label = (
                "分池目标簿买入"
                if not respect_max_positions
                else "横截面调仓买入（TopK）"
            )
            trade = annotate_trade(
                {
                    "ts": _now_iso(),
                    "side": "buy",
                    "stock_code": code,
                    "stock_name": item.get("stock_name") or quote.get("stock_name"),
                    "shares": shares,
                    "price": round(fill_px, 4),
                    "amount": amount,
                    "score": score,
                    "origin": ORIGIN_STRATEGY,
                    "note": f"{buy_label}{tw_note}{clip_note}{to_note}",
                    "target_weight_pct": target_w.get(code),
                    "sector": sector,
                    "risk_budget_clipped": bool(clip.get("clipped")),
                    "turnover_budget_clipped": bool(_to_clipped),
                },
                fee_info,
            )
            paper.setdefault("trades", []).append(trade)
            buy_trades.append(trade)
            holdings.append(
                {
                    "stock_code": code,
                    "stock_name": trade["stock_name"],
                    "shares": shares,
                    "cost": round(fill_px, 4),
                    "bought_at": trade["ts"],
                    "origin": ORIGIN_STRATEGY,
                    "sector": sector,
                    "market_value": amount,
                }
            )
            held_codes.add(code)
            cash = round(cash + float(fee_info["net_cash_delta"]), 2)
            name_mv[code] = float(name_mv.get(code) or 0.0) + amount
            sector_mv[sector] = float(sector_mv.get(sector) or 0.0) + amount

    # P3-1：换手预算背包再分配 — 首轮被软上限跳过的候选，用半仓在剩余换手预算内重试
    # top_items 已按 score 降序，retry_pool 继承该序；高分离票优先榨干剩余预算
    _retry_fitted = 0
    if (
        _turnover_retry_pool
        and max_turnover_pct is not None
        and equity_before
        and equity_before > 0
    ):
        for _rc in _turnover_retry_pool:
            # 分池：目标簿已持只数达上限则停；横截面：总持仓达上限则停
            if not respect_max_positions:
                _book_held = sum(
                    1 for _h in holdings
                    if str(_h.get("stock_code") or "") in top_codes
                )
                if _book_held >= max_positions:
                    break
            elif len(holdings) >= max_positions:
                break

            _code = _rc["code"]
            if _code in held_codes or _code in force_trim_no_rebuy:
                continue
            _item = _rc["item"]
            _price = _rc["price"]
            _quote = _rc["quote"]
            _sector = _rc["sector"]
            _score = _rc["score"]
            if not _price or _price <= 0:
                continue

            # 半仓：base_ratio 折半，再叠 sentiment / target_w 收紧
            _ratio = max(0.0, float(_rc.get("base_ratio") or 0.0) * 0.5)
            _pa_ratio = _rc.get("prior_apply_ratio")
            if _pa_ratio is not None:
                try:
                    _ratio = min(_ratio, float(_pa_ratio))
                except (TypeError, ValueError):
                    pass
            if _rc.get("target_w_pct") is not None:
                try:
                    _ratio = min(_ratio, float(_rc["target_w_pct"]) / 100.0)
                except (TypeError, ValueError):
                    pass
            if _ratio <= 0:
                continue
            if not respect_max_positions and budget_equity > 0:
                _budget = min(cash, budget_equity * _ratio)
            else:
                _budget = cash * _ratio
            if _budget < _price * 100:
                continue
            _shares = int(_budget // _price // 100) * 100
            if _shares <= 0:
                continue

            _clip = clip_buy_to_risk_budget(
                code=_code,
                sector=_sector,
                price=float(_price),
                shares=_shares,
                equity=budget_equity,
                name_mv=name_mv,
                sector_mv=sector_mv,
                max_position_pct=max_pos_pct,
                max_sector_pct=max_sec_pct,
            )
            if _clip.get("skipped"):
                continue
            _shares = int(_clip.get("shares") or _shares)

            _fill_px = apply_fill_price(
                "buy", float(_price), model=cost_model, params=fee_params
            )
            _amount = round(_shares * _fill_px, 2)
            _fee_info = calc_trade_fees(
                "buy", _amount, model=cost_model, params=fee_params
            )
            _need = _amount + float(_fee_info.get("fees") or 0)
            if _need > cash + 1e-6:
                continue

            # 换手软上限复检 + R5 双边拆分；超限则裁剪到剩余预算
            _max_to = float(max_turnover_pct) if max_turnover_pct is not None else None
            _to_retry_note = "换手背包半仓补入"
            if _max_to is not None and equity_before and equity_before > 0:
                _sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
                _ss_pct = _max_to / 2.0
                _ss_amt = _ss_pct / 100.0 * equity_before
                _s_excess = max(0.0, _ss_amt - _sell_amt)
                _buy_sofar = sum(float(t.get("amount") or 0) for t in buy_trades)
                _buy_budget = _ss_amt + min(_s_excess, _ss_amt)
                _buy_after = _buy_sofar + _amount
                _proj_to = (_sell_amt + _buy_after) / 2.0 / equity_before * 100.0
                _b_over = _buy_after > _buy_budget + 1e-9
                _t_over = _proj_to > _max_to + 1e-9
                if _b_over or _t_over:
                    _clipped = clip_shares_to_turnover_budget(
                        shares=_shares,
                        fill_px=_fill_px,
                        buy_amt_so_far=_buy_sofar,
                        buy_budget_amt=_buy_budget,
                        sell_amt=_sell_amt,
                        equity_before=equity_before,
                        max_turnover_pct=_max_to,
                    )
                    if _clipped < 100:
                        continue
                    _shares = _clipped
                    _amount = round(_shares * _fill_px, 2)
                    _fee_info = calc_trade_fees(
                        "buy", _amount, model=cost_model, params=fee_params
                    )
                    _need = _amount + float(_fee_info.get("fees") or 0)
                    if _need > cash + 1e-6:
                        continue
                    _to_retry_note = "换手背包半仓补入 · 预算裁剪"
            else:
                _sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
                _buy_amt = sum(float(t.get("amount") or 0) for t in buy_trades) + _amount
                _proj_to = (_sell_amt + _buy_amt) / 2.0 / equity_before * 100.0
                if max_turnover_pct is not None and _proj_to > float(max_turnover_pct) + 1e-9:
                    continue

            _tw_note = ""
            if _code in target_w:
                _tw_note = f" \u00b7 目标仓{float(target_w[_code]):.1f}%"
            _clip_note = f" \u00b7 {_clip['reason']}" if _clip.get("clipped") else ""
            _trade = annotate_trade(
                {
                    "ts": _now_iso(),
                    "side": "buy",
                    "stock_code": _code,
                    "stock_name": _item.get("stock_name") or _quote.get("stock_name"),
                    "shares": _shares,
                    "price": round(_fill_px, 4),
                    "amount": _amount,
                    "score": _score,
                    "origin": ORIGIN_STRATEGY,
                    "note": f"{_to_retry_note}{_tw_note}{_clip_note}",
                    "target_weight_pct": target_w.get(_code),
                    "sector": _sector,
                    "risk_budget_clipped": bool(_clip.get("clipped")),
                    "turnover_reallocate": True,
                },
                _fee_info,
            )
            paper.setdefault("trades", []).append(_trade)
            buy_trades.append(_trade)
            holdings.append(
                {
                    "stock_code": _code,
                    "stock_name": _trade["stock_name"],
                    "shares": _shares,
                    "cost": round(_fill_px, 4),
                    "bought_at": _trade["ts"],
                    "origin": ORIGIN_STRATEGY,
                    "sector": _sector,
                    "market_value": _amount,
                }
            )
            held_codes.add(_code)
            cash = round(cash + float(_fee_info["net_cash_delta"]), 2)
            name_mv[_code] = float(name_mv.get(_code) or 0.0) + _amount
            sector_mv[_sector] = float(sector_mv.get(_sector) or 0.0) + _amount
            _retry_fitted += 1

    if _retry_fitted > 0:
        append_operation_log(
            paper,
            "turnover_reallocate",
            detail=f"换手背包再分配：半仓补入 {_retry_fitted} 只",
            meta={"fitted_count": _retry_fitted},
        )

    # 风控拦截时仍给出目标权重建议
    if buys_blocked and not paper.get("last_optimize"):
        try:
            from core.portfolio_optimize import optimize_weights
            from core.strategy import get_strategy_spec

            sid = paper.get("strategy_id") or "short"
            rr = (get_strategy_spec(str(sid)).get("risk") or {})
            paper["last_optimize"] = optimize_weights(
                list(ranking or []),
                max_position_pct=float(rr.get("max_position_pct") or 25.0),
                max_sector_pct=float(rr.get("max_sector_pct") or 40.0),
                max_positions=int(rr.get("max_positions") or max_positions),
                min_score=float(min_score),
            )
        except Exception:
            paper["last_optimize"] = None

    paper["holdings"] = holdings
    paper["cash"] = round(cash, 2)
    paper["updated_at"] = _now_iso()

    if turnover_capped and turnover_skipped:
        append_operation_log(
            paper,
            "turnover_cap",
            detail=(
                f"换手软上限 {max_turnover_pct}% 跳过买入 "
                + "、".join(turnover_skipped[:8])
                + (f" 等{len(turnover_skipped)}只" if len(turnover_skipped) > 8 else "")
            ),
            meta={
                "max_turnover_pct": max_turnover_pct,
                "skipped_codes": turnover_skipped,
                "path": "cluster" if not respect_max_positions else "cross_section",
            },
        )

    if sell_match_skips:
        append_operation_log(
            paper,
            "sell_match_skips",
            detail=(
                "卖出侧涨跌停/停牌跳过 "
                + "、".join(s.get("stock_code", "?") for s in sell_match_skips[:8])
                + (f" 等{len(sell_match_skips)}只" if len(sell_match_skips) > 8 else "")
            ),
            meta={
                "skips": sell_match_skips,
                "path": "sell_match",
            },
        )

    dq = None
    try:
        from core.data_service import summarize_data_quality

        codes = [str(c) for c in sorted(top_codes) if c][:12]
        if codes:
            raw_dq = summarize_data_quality(codes, limit=40)
            dq = {
                "levels": raw_dq.get("levels"),
                "fallback_count": raw_dq.get("fallback_count"),
                "gated_count": raw_dq.get("gated_count"),
                "count": raw_dq.get("count"),
                "adjust_policy": raw_dq.get("adjust_policy"),
            }
    except Exception:
        dq = None
    # 使用模块顶层 resolve_cost_model；勿在函数内再 import 同名，否则整函数变 local 未绑定
    cost_model = resolve_cost_model(paper)

    risk_blocks = (risk_gate or {}).get("blocks") or []
    monitor_alerts: list = []
    health: dict = {}
    summary: Dict[str, Any] = {}
    try:
        from core.paper import mark_to_market
        from core.strategy_monitor import assess_strategy_health

        summary = mark_to_market(paper)
        codes = [
            str(h.get("stock_code") or "").strip()
            for h in (paper.get("holdings") or [])
            if str(h.get("stock_code") or "").strip()
        ]
        for r in ranking or []:
            c = str(r.get("stock_code") or "").strip()
            if c and c not in codes:
                codes.append(c)
        health = assess_strategy_health(
            paper,
            summary=summary,
            compute_rolling_ic=True,
            codes=codes[:12],
        )
        monitor_alerts = list(health.get("alerts") or [])
    except Exception:
        monitor_alerts = []
        health = {}
    metrics = health.get("metrics") if isinstance(health, dict) else None
    north_star = None
    source_audit = None
    try:
        from core.north_star import merge_north_star_into_metrics

        metrics, north_star = merge_north_star_into_metrics(paper, metrics)
        paper["last_north_star"] = north_star
    except Exception:
        pass
    try:
        from core.data_consistency import audit_code_sources

        codes_audit = [
            str(h.get("stock_code") or "").strip()
            for h in (paper.get("holdings") or [])
            if str(h.get("stock_code") or "").strip()
        ]
        source_audit = audit_code_sources(codes_audit)
    except Exception:
        source_audit = None
    if not summary:
        try:
            from core.paper import mark_to_market as _mtm1

            summary = _mtm1(paper) or {}
        except Exception:
            summary = {}

    attribution: Dict[str, Any] = {}
    try:
        from core.paper_attribution import build_paper_attribution_lite

        attribution = build_paper_attribution_lite(paper, summary) or {}
    except Exception:
        attribution = {"ok": False, "reason": "attribution_error"}

    ops_report = build_ops_report(
        strategy_id=paper.get("strategy_id"),
        strategy_version=paper.get("strategy_version"),
        cost_model=cost_model,
        data_quality=dq,
        risk_blocks=risk_blocks,
        monitor_alerts=monitor_alerts,
        buys_blocked=buys_blocked,
        optimize=paper.get("last_optimize"),
        risk_limits=(risk_gate or {}).get("limits"),
        monitor_metrics=metrics,
        north_star=north_star,
        source_audit=source_audit,
        exposure=(risk_gate or {}).get("exposure"),
        risk_block_items=(risk_gate or {}).get("block_items"),
        attribution=attribution,
    )
    paper["last_ops_report"] = ops_report

    cash_impact = build_rebalance_cash_impact(
        cash_before=cash_before,
        position_count_before=position_count_before,
        sell_trades=sell_trades,
        buy_trades=buy_trades,
        summary=summary,
        equity_before=equity_before,
        cost_model=cost_model,
        max_turnover_pct=max_turnover_pct,
        turnover_capped=turnover_capped,
    )
    turnover = compute_turnover_stats(
        sell_trades,
        buy_trades,
        equity_before=equity_before,
        max_turnover_pct=max_turnover_pct,
    )

    from core.signal.score_display import json_safe_number

    cost_assumptions = {
        "cost_model": cost_model,
        "fee_params": {
            k: fee_params.get(k)
            for k in ("commission_rate", "min_commission", "stamp_duty_rate", "slippage_bps", "max_slippage_bps")
            if isinstance(fee_params, dict) and k in fee_params
        }
        if isinstance(fee_params, dict)
        else {},
        "turnover_pct": turnover.get("turnover_pct"),
        "max_turnover_pct": max_turnover_pct,
        "weight_mode": (paper.get("last_optimize") or {}).get("weight_mode")
        or (rules.get("weight_mode") if isinstance(rules, dict) else None)
        or "score_budget",
        "note": "分池/横截面调仓成本假设；与回测页对照见 fit-gap",
    }

    # Y3.1：持仓风格/规模暴露简表
    exposure_style = None
    try:
        from core.risk.exposure import build_exposure_matrix

        exposure_style = build_exposure_matrix(paper, summary)
    except Exception:
        exposure_style = None

    try:
        from core.signal.dual_score import get_dual_score_cfg

        _cfg_dual = get_dual_score_cfg()
        _dual_meta = {
            "fusion_mode": _cfg_dual.get("fusion_mode"),
            "min_predicted_score_tau": _cfg_dual.get("min_predicted_score_tau"),
            "tau_gate": _tau_floor_meta or None,
            "note": (
                "排序=ŷ_trade（raw）；买入门槛=ŷ_EOD≥min 且 ŷ_τ≥floor；校准 g 仅 tip 对照"
                + (
                    f"；{_tau_floor_meta.get('note')}"
                    if _tau_floor_meta.get("note")
                    else ""
                )
            ),
        }
    except Exception:
        _dual_meta = {"fusion_mode": None, "note": "dual_score unavailable"}

    empty_reason = None
    if not buy_trades and not sell_trades:
        floor_skips = [
            s
            for s in (risk_budget_skips or [])
            if str(s.get("reason") or "")
            in ("below_eod_floor", "oos_failed_no_buy")
            or "floor" in str(s.get("reason") or "").lower()
            or "门槛" in str(s.get("reason") or "")
        ]
        if not ranking and not top_codes:
            empty_reason = "empty_ranking"
        elif floor_skips and not buy_trades:
            empty_reason = "all_below_eod_floor"
        elif buys_blocked:
            empty_reason = "buys_blocked"
        elif risk_blocks:
            empty_reason = "risk_blocked"
        else:
            empty_reason = "no_executable_changes"
        if empty_reason:
            warns = list(risk_gate.get("warnings") or [])
            msg = (
                f"无可执行变动（{empty_reason}）；"
                f"min_predicted_score={min_score}；"
                f"floor_skips={len(floor_skips)} / ranking={len(ranking or [])}"
            )
            if msg not in warns:
                warns.append(msg)
            risk_gate["warnings"] = warns

    return {
        "success": True,
        "top_k": top_k,
        "target_codes": sorted(top_codes),
        "min_score": json_safe_number(min_score),
        "min_hold_score": json_safe_number(min_hold_score),
        "empty_reason": empty_reason,
        "sell_trades": sell_trades,
        "buy_trades": buy_trades,
        "sentiment_restore_trades": sentiment_restore_trades,
        "buys_blocked": buys_blocked,
        "risk_gate": risk_gate,
        "risk_blocks": risk_blocks,
        "data_quality": dq or {},
        "cost_model": cost_model,
        "ops_report": ops_report,
        "last_optimize": paper.get("last_optimize"),
        "target_weights": (paper.get("last_optimize") or {}).get("weights_pct"),
        "cash_impact": cash_impact,
        "turnover": turnover,
        "turnover_capped": turnover_capped,
        "turnover_skipped": turnover_skipped,
        "max_turnover_pct": max_turnover_pct,
        "risk_budget_skips": risk_budget_skips,
        "sentiment_prior": sentiment_prior_summary,
        "event_prior": {
            "soft_holds": event_prior_soft_holds,
            "count": len(event_prior_soft_holds),
            "note": "主题开盘缺口日：低 ŷ 卖出改为 soft hold（不改 ŷ_EOD）",
        },
        "dual_score": _dual_meta,
        "attribution": attribution,
        "cost_assumptions": cost_assumptions,
        "exposure_style": exposure_style,
        "note": "横截面/分池调仓为纸面模拟；排序=ŷ_trade（raw）；买入=EOD门槛且 τ 闸（均 raw）；卖出=ŷ_trade 低于 min_hold；校准 g 仅 tip。",
    }
