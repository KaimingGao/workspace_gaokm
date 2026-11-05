"""模拟账户账本（内部 canonical 名 paper；产品对外称「模拟」）。

非实盘。定量见 paper_sizing；成本见 paper_costs；行情经 core.ports。
"""


import logging

logger = logging.getLogger(__name__)
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterator, List, Optional, TypeVar

from core.file_lock import path_lock
from core.io_atomic import atomic_write_json
from core.paper.costs import resolve_cost_model  # noqa: F401 — re-export for paper_cycle / callers
from core.paper.sizing import (  # noqa: F401 — re-export for watching_store / callers
    DEFAULT_SYNC_AMOUNT,
    DEFAULT_SYNC_LOT_SHARES,
    buy_codes_direct,
    plan_buy_codes,
)
from core.paths import PAPER_EXAMPLE_PATH, PAPER_PATH
from core.ports.market import quote_price
from core.ports.signal import build_signal_pool

DEFAULT_PAPER_PATH = PAPER_PATH
EXAMPLE_PATH = PAPER_EXAMPLE_PATH

# 账本列表上限：防长期运行 JSON 膨胀（与 operation_log/snapshots 对齐）
MAX_TRADES = 500
MAX_SNAPSHOTS = 120
# 每个会话只留收盘净值，供「今日收益 = 今日净值 − 昨收净值」。不跟盘中快照一起裁。
MAX_SESSION_NAV = 400
MAX_SIGNAL_LOG = 30
MAX_OPERATION_LOG = 200

T = TypeVar("T")


@contextmanager
def paper_write_lock(path: Optional[str] = None) -> Iterator[None]:
    """串行化对本 paper 路径的 RMW（进程内 + 跨进程文件锁）。"""
    p = path or DEFAULT_PAPER_PATH
    with path_lock(p):
        yield


def trim_paper_lists(paper: dict) -> dict:
    """就地裁剪 trades / snapshots / logs，控制 paper.json 体积。"""
    if not isinstance(paper, dict):
        return paper
    trades = paper.get("trades")
    if isinstance(trades, list) and len(trades) > MAX_TRADES:
        paper["trades"] = trades[-MAX_TRADES:]
    snaps = paper.get("snapshots")
    if isinstance(snaps, list):
        fold_snapshots_into_session_nav(paper)
        if len(snaps) > MAX_SNAPSHOTS:
            paper["snapshots"] = snaps[-MAX_SNAPSHOTS:]
    slog = paper.get("signal_log")
    if isinstance(slog, list) and len(slog) > MAX_SIGNAL_LOG:
        paper["signal_log"] = slog[-MAX_SIGNAL_LOG:]
    olog = paper.get("operation_log")
    if isinstance(olog, list) and len(olog) > MAX_OPERATION_LOG:
        paper["operation_log"] = olog[-MAX_OPERATION_LOG:]
    return paper


def mutate_paper(
    mutator: Callable[[dict], T],
    path: Optional[str] = None,
) -> T:
    """加锁 → load → mutator(paper) → trim → save。mutator 可返回附加结果。"""
    p = path or DEFAULT_PAPER_PATH
    with paper_write_lock(p):
        paper = load_paper(p)
        result = mutator(paper)
        trim_paper_lists(paper)
        save_paper(paper, p)
        return result


def _now_iso() -> str:
    """当前时间；纸面回放上下文内固定为会话日开盘，保证 T+1 lots 日期正确。"""
    try:
        from core.paper.replay_ctx import replay_as_of

        day = replay_as_of()
        if day:
            return f"{day}T09:30:00.000"
    except Exception:  # noqa: BLE001 — 回放时钟不可用时回退真实时间
        logger.debug("replay_as_of for _now_iso failed", exc_info=True)
    return datetime.now().isoformat(timespec="milliseconds")


def _next_snapshot_ts(paper: dict) -> str:
    """相对上一笔快照单调递增，避免买卖前/后同毫秒撞 LWC time。"""
    ts = _now_iso()
    snaps = paper.get("snapshots") or []
    if not snaps:
        return ts
    last = str((snaps[-1] or {}).get("ts") or "")
    if not last or ts > last:
        return ts
    try:
        return (datetime.fromisoformat(last) + timedelta(milliseconds=1)).isoformat(
            timespec="milliseconds"
        )
    except ValueError:
        return ts


# 毒字段：ŷ 门槛由 score_display.resolve_*_floor 统一管理，禁止留在 paper.rules（C2 / P-V1）
_TOXIC_RULE_KEYS = ("min_score", "min_hold_score")


def validate_paper(data: dict, *, on_load: bool = True) -> List[str]:
    """就地规范化 paper dict；返回 warnings（不写进 paper.json）。

    硬：cash 缺失/非有限数；holdings/trades/logs 类型；rules 须为 dict。
    软：剥离 rules.min_score / min_hold_score；清洗非法限额标量。
    """
    if not isinstance(data, dict):
        raise ValueError("paper 必须为 dict")
    if "cash" not in data:
        raise ValueError("paper.json 缺少 cash")
    try:
        cash_f = float(data["cash"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"paper.cash 无法解析为数字: {data.get('cash')!r}") from exc
    if cash_f != cash_f or cash_f in (float("inf"), float("-inf")):
        raise ValueError(f"paper.cash 非有限数: {data.get('cash')!r}")
    data["cash"] = cash_f

    warnings: List[str] = []

    for key in ("holdings", "trades", "signal_log", "snapshots", "operation_log"):
        if key in data and data[key] is not None and not isinstance(data[key], list):
            raise ValueError(f"paper.{key} 必须为 list")

    if "rules" in data and data["rules"] is not None and not isinstance(data["rules"], dict):
        raise ValueError("paper.rules 必须为 dict")

    rules = data.get("rules")
    if isinstance(rules, dict):
        for tk in _TOXIC_RULE_KEYS:
            if tk in rules:
                rules.pop(tk, None)
                warnings.append(f"stripped rules.{tk} (ŷ path; use scoring.min_predicted_score)")
        # 非法限额标量：丢掉，交给 resolve_account_risk_limits 用 Spec/DEFAULT
        for rk, kind in (
            ("max_positions", "int"),
            ("max_position_pct", "float"),
            ("max_sector_pct", "float"),
            ("max_drawdown_pct", "float"),
            ("position_pct", "float"),
        ):
            if rk not in rules or rules[rk] is None:
                continue
            raw = rules[rk]
            try:
                if kind == "int":
                    if isinstance(raw, bool):
                        raise ValueError("bool")
                    iv = int(raw)
                    if iv < 1:
                        raise ValueError("lt1")
                    rules[rk] = iv
                else:
                    fv = float(raw)
                    if fv != fv or fv in (float("inf"), float("-inf")):
                        raise ValueError("nan")
                    rules[rk] = fv
            except (TypeError, ValueError):
                rules.pop(rk, None)
                warnings.append(f"dropped rules.{rk} (invalid: {raw!r})")

    if on_load:
        for w in warnings:
            logger.warning("validate_paper: %s", w)
    return warnings


def load_paper(path: Optional[str] = None) -> dict:
    p = path or DEFAULT_PAPER_PATH
    if not os.path.isfile(p):
        raise FileNotFoundError(f"模拟账户不存在: {p}（可先初始化）")
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    data.pop("watchlist", None)  # 已废弃：名单只在 watching，仓位只在 holdings
    data.setdefault("holdings", [])
    data.setdefault("trades", [])
    data.setdefault("signal_log", [])
    data.setdefault("snapshots", [])
    data.setdefault("operation_log", [])
    data.setdefault("rules", {})
    data.setdefault("cost_model", "simple_cn")
    data.setdefault("cost_params", {})
    # cash 类型/缺失由 validate_paper 硬校验
    validate_paper(data, on_load=True)
    try:
        from core.t0.intraday import stamp_resolved_stock_names

        stamp_resolved_stock_names(data.get("holdings"))
        stamp_resolved_stock_names(data.get("trades"))
        stamp_resolved_stock_names(data.get("operation_log"))
    except Exception:  # noqa: BLE001
        logger.debug("stamp paper stock names skipped", exc_info=True)
    return data


def holding_codes(paper: dict) -> List[str]:
    """模拟持仓代码（去重、保序）。"""
    out: List[str] = []
    seen = set()
    for h in paper.get("holdings") or []:
        c = str(h.get("stock_code") or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def save_paper(data: dict, path: Optional[str] = None) -> str:
    p = path or DEFAULT_PAPER_PATH
    data.pop("watchlist", None)
    # 写盘前 strip 毒字段；warnings 仅日志，不落盘
    validate_paper(data, on_load=False)
    trim_paper_lists(data)
    with paper_write_lock(p):
        atomic_write_json(p, data)
    return p


def init_from_example(path: Optional[str] = None) -> str:
    p = path or DEFAULT_PAPER_PATH
    if os.path.isfile(p):
        raise FileExistsError(f"已存在: {p}")
    with open(EXAMPLE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("cost_model", "simple_cn")
    data["created_at"] = _now_iso()
    data["updated_at"] = data["created_at"]
    return save_paper(data, p)


def _quote_price(quote: dict) -> Optional[float]:
    """兼容旧调用；新代码请用 core.ports.market.quote_price。"""
    return quote_price(quote)


def run_signal_scan(paper: dict, *, on_progress=None, stock_codes=None) -> List[dict]:
    """对指定股票列表跑 signal，写入 signal_log。

    未传 stock_codes 时：优先持仓；无持仓则用观察池 watching。
    """
    if stock_codes is not None:
        codes = list(stock_codes)
    else:
        codes = holding_codes(paper)
        if not codes:
            try:
                from core.watching.store import read_watching

                codes = [
                    str(c).strip()
                    for c in (read_watching().get("watchlist") or [])
                    if str(c).strip()
                ]
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                codes = []
    if not codes:
        return []

    rules = paper.get("rules") or {}
    horizon = int(rules.get("horizon_days") or 3)
    limit = int(rules.get("signal_limit") or 8)

    payload = build_signal_pool(
        {
            "stock_codes": codes,
            "horizon_days": horizon,
            "limit": limit,
            "skip_fundamentals": True,
        },
        on_progress=on_progress,
    )
    ts = _now_iso()
    entry = {
        "ts": ts,
        "success": payload.get("success"),
        "horizon_days": horizon,
        "observation_pool": payload.get("observation_pool") or [],
        "rejected_sample": payload.get("rejected_sample") or [],
        "error": payload.get("error"),
    }
    paper.setdefault("signal_log", []).append(entry)
    paper["signal_log"] = paper["signal_log"][-MAX_SIGNAL_LOG:]
    paper["updated_at"] = ts
    return payload.get("scored_items") or payload.get("observation_pool") or []

ORIGIN_MANUAL = "manual"
ORIGIN_STRATEGY = "strategy"
ORIGIN_MIXED = "mixed"
ORIGIN_LABELS = {
    ORIGIN_MANUAL: "手动",
    ORIGIN_STRATEGY: "策略",
    ORIGIN_MIXED: "手动+策略",
}


def merge_origin(existing: Optional[str], incoming: str) -> str:
    """同一仓位被手动与策略先后加过时标为 mixed，避免出处失真。"""
    current = str(existing or "").strip()
    if not current or current == incoming:
        return incoming
    return ORIGIN_MIXED


def remember_session_nav(paper: dict, ts: Any, equity: Any) -> None:
    """记下该会话最后一笔账户净值。盘中快照被裁掉后，今日收益仍相对昨收净值。"""
    if not isinstance(paper, dict):
        return
    day = str(ts or "")[:10]
    if len(day) != 10:
        return
    try:
        eq = round(float(equity), 2)
    except (TypeError, ValueError):
        return
    if eq <= 1e-6:
        return
    book = paper.get("session_nav")
    if not isinstance(book, dict):
        book = {}
        paper["session_nav"] = book
    book[day] = eq
    if len(book) > MAX_SESSION_NAV:
        for key in sorted(str(k) for k in book)[:-MAX_SESSION_NAV]:
            book.pop(key, None)


def fold_snapshots_into_session_nav(paper: dict) -> None:
    """把现有快照按日收成 session_nav。同一日以后写的点为准。"""
    if not isinstance(paper, dict):
        return
    for snap in paper.get("snapshots") or []:
        if not isinstance(snap, dict):
            continue
        remember_session_nav(
            paper,
            snap.get("ts") or snap.get("date"),
            snap.get("equity"),
        )


def append_snapshot(paper: dict, summary: Dict[str, Any]) -> None:
    snap = {
        "ts": _next_snapshot_ts(paper),
        "equity": summary.get("equity"),
        "cash": summary.get("cash"),
        "stock_value": summary.get("stock_value"),
        "total_pnl_pct": summary.get("total_pnl_pct"),
        "position_count": summary.get("position_count"),
        # E0：策略仓归因净值（无策略仓则为 None，北极星 strategy scope 跳过）
        "equity_strategy": summary.get("equity_strategy"),
        "strategy_stock_value": summary.get("strategy_stock_value"),
    }
    paper.setdefault("snapshots", []).append(snap)
    remember_session_nav(paper, snap["ts"], snap.get("equity"))
    paper["snapshots"] = paper["snapshots"][-MAX_SNAPSHOTS:]


def snapshots_for_ui(
    paper: dict,
    summary: Optional[Dict[str, Any]] = None,
    *,
    tol: float = 0.01,
) -> List[dict]:
    """落盘 snapshots + 未落盘的现价盯市末点（只读，不写 paper.json）。

    曲线只在成交/调仓/资金变动时落点；盘中报价会变，摘要总净值走
    ``mark_to_market``，末点若不叠现价就会和 KPI 对不上。
    """
    snaps = [s for s in (paper.get("snapshots") or []) if isinstance(s, dict)]
    if not isinstance(summary, dict):
        return snaps
    live_eq = summary.get("equity")
    if live_eq is None:
        return snaps
    try:
        live_f = float(live_eq)
    except (TypeError, ValueError):
        return snaps
    last_eq: Optional[float] = None
    if snaps:
        try:
            last_eq = float(snaps[-1].get("equity"))
        except (TypeError, ValueError):
            last_eq = None
    if last_eq is not None and abs(last_eq - live_f) <= float(tol):
        return snaps
    overlay = {
        "ts": _next_snapshot_ts({"snapshots": snaps}),
        "equity": round(live_f, 2),
        "cash": summary.get("cash"),
        "stock_value": summary.get("stock_value"),
        "total_pnl_pct": summary.get("total_pnl_pct"),
        "position_count": summary.get("position_count"),
        "equity_strategy": summary.get("equity_strategy"),
        "strategy_stock_value": summary.get("strategy_stock_value"),
        "live": True,
    }
    return snaps + [overlay]


def mark_to_market(paper: dict) -> Dict[str, Any]:
    """盯市快照。实现在 exec；此处再导出，供调仓等 ``from core.paper.ledger import mark_to_market``。"""
    from core.paper.exec import mark_to_market as _mark_to_market

    return _mark_to_market(paper)


def capture_mark_snapshot(paper: dict) -> Dict[str, Any]:
    """改仓前盯市落点：把未实现涨跌从成交快照里拆开，避免曲线「一卖就跳」。"""
    summary = mark_to_market(paper)
    append_snapshot(paper, summary)
    return summary


# 成交流水。回零与「清除交易记录」共用；资金流水（init/deposit/withdraw/reset）不在此列。
TRADING_OPERATION_TYPES = frozenset(
    {
        "buy",
        "sell",
        "rebalance",
        "watching_matrix_rebalance",
        "t0_batch",
        "t0_void",
        "sync_paper",
    }
)


OPERATION_LOG_TYPES = {
    "init": "初始化",
    "deposit": "注资",
    "withdraw": "减资",
    "reset": "回零",
    "buy": "买入",
    "sell": "卖出",
    "rebalance": "调仓",
    "watching_matrix_rebalance": "调仓",
    "t0_batch": "做T汇总",
    "t0_void": "做T冲正",
    "sync_paper": "建仓",
    "settings": "设置",
    "risk_block": "风控拦截",
}


def build_ops_report(
    *,
    strategy_id: Optional[str] = None,
    strategy_version: Optional[str] = None,
    cost_model: Optional[str] = None,
    data_quality: Optional[Dict[str, Any]] = None,
    risk_blocks: Optional[List[Any]] = None,
    monitor_alerts: Optional[List[Any]] = None,
    buys_blocked: bool = False,
    target_weights: Optional[Dict[str, Any]] = None,
    risk_limits: Optional[Dict[str, Any]] = None,
    optimize: Optional[Dict[str, Any]] = None,
    monitor_metrics: Optional[Dict[str, Any]] = None,
    north_star: Optional[Dict[str, Any]] = None,
    source_audit: Optional[Dict[str, Any]] = None,
    exposure: Optional[Dict[str, Any]] = None,
    risk_block_items: Optional[List[Any]] = None,
    attribution: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """北极星日更/调仓「五问」稳定结构（可进响应顶层与 DecisionRecord）。"""
    dq = data_quality or {}
    tw = target_weights
    if tw is None and isinstance(optimize, dict):
        tw = optimize.get("weights_pct")
    limits = risk_limits
    if limits is None and isinstance(optimize, dict):
        limits = optimize.get("limits")
    out: Dict[str, Any] = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "cost_model": cost_model,
        "data_quality": dq,
        "risk_blocks": list(risk_blocks or []),
        "monitor_alerts": list(monitor_alerts or []),
        "buys_blocked": bool(buys_blocked),
        "fallback_count": int(dq.get("fallback_count") or 0),
    }
    if strategy_id:
        try:
            from core.strategy import get_strategy_spec

            out["strategy_label"] = get_strategy_spec(str(strategy_id)).get("label")
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            out["strategy_label"] = None
    if tw is not None:
        out["target_weights"] = tw
    if limits is not None:
        out["risk_limits"] = limits
    if isinstance(optimize, dict):
        out["optimize"] = {
            "ok": optimize.get("ok"),
            "total_pct": optimize.get("total_pct"),
            "count": optimize.get("count"),
            "sector_exposure_pct": optimize.get("sector_exposure_pct"),
            "skipped_count": len(optimize.get("skipped") or []),
            "weight_mode": optimize.get("weight_mode"),
            "vol_scale": optimize.get("vol_scale"),
            "budget_alerts": (optimize.get("budget_alerts") or [])[:5],
        }
    if isinstance(monitor_metrics, dict) and monitor_metrics:
        out["monitor_metrics"] = {
            k: monitor_metrics.get(k)
            for k in (
                "rolling_ic",
                "sector_coverage",
                "max_drawdown_pct",
                "rolling_sharpe",
                "calmar",
                "realization_corr",
                "tracking_error_pct",
            )
            if k in monitor_metrics
        }
    if isinstance(north_star, dict) and north_star:
        pr = north_star.get("paper_risk") or {}
        rz = north_star.get("realization") or {}
        ttm = north_star.get("ttm") or {}
        rb = north_star.get("risk_blocks") or {}
        out["north_star"] = {
            "rolling_sharpe": pr.get("rolling_sharpe"),
            "calmar": pr.get("calmar"),
            "corr": rz.get("corr"),
            "tracking_error_pct": rz.get("tracking_error_pct"),
            "ttm_median_idea_to_paper_hours": ttm.get("median_idea_to_paper_hours"),
            "risk_block_count": rb.get("block_count"),
            "risk_effectiveness_rate": rb.get("effectiveness_rate"),
            "risk_false_block_rate": rb.get("false_block_rate"),
            "risk_by_reason": rb.get("by_reason"),
            "status": {
                "paper_risk": pr.get("status"),
                "realization": rz.get("status"),
                "ttm": ttm.get("status"),
                "risk_blocks": rb.get("status"),
            },
        }
    if isinstance(source_audit, dict) and source_audit:
        out["source_audit"] = {
            "status": source_audit.get("status"),
            "fallback_count": source_audit.get("fallback_count"),
            "fallback_codes": (source_audit.get("fallback_codes") or [])[:8],
            "mismatch_hint": source_audit.get("mismatch_hint"),
        }
    if isinstance(exposure, dict) and exposure:
        out["exposure"] = {
            "sectors": (exposure.get("sectors") or [])[:12],
            "styles": (exposure.get("styles") or [])[:8],
            "size_buckets": (exposure.get("size_buckets") or [])[:6],
            "over_limit_sectors": exposure.get("over_limit_sectors") or [],
            "over_limit_names": exposure.get("over_limit_names") or [],
            "limits": exposure.get("limits"),
            "note": exposure.get("note"),
        }
    if risk_block_items:
        out["risk_block_items"] = list(risk_block_items)[:20]
    if isinstance(attribution, dict) and attribution:
        out["attribution"] = {
            "ok": attribution.get("ok"),
            "mode": attribution.get("mode"),
            "selection_pct": attribution.get("selection_pct"),
            "allocation_pct": attribution.get("allocation_pct"),
            "residual_pct": attribution.get("residual_pct"),
            "total_excess_pct": attribution.get("total_excess_pct"),
            "portfolio_return_pct": attribution.get("portfolio_return_pct"),
            "period_return_pct": attribution.get("period_return_pct"),
            "by_sector": (attribution.get("by_sector") or [])[:6],
            "top_contributors": (attribution.get("top_contributors") or [])[:5],
            "name_count": attribution.get("name_count"),
            "methodology": attribution.get("methodology"),
            "note": attribution.get("note"),
        }
    return out


def append_operation_log(
    paper: dict,
    op_type: str,
    *,
    detail: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    entry = {
        "ts": _now_iso(),
        "type": op_type,
        "type_label": OPERATION_LOG_TYPES.get(op_type, op_type),
        "detail": detail or "",
        "meta": meta or {},
    }
    paper.setdefault("operation_log", []).append(entry)
    paper["operation_log"] = paper["operation_log"][-MAX_OPERATION_LOG:]


def _trade_side_for_log(raw_side: str) -> str:
    side = str(raw_side or "").strip().lower()
    if side == "t0_sell":
        return "sell"
    if side == "t0_buy":
        return "buy"
    return side


def trade_leg_operation_entry(
    trade: dict,
    *,
    origin: str = "cluster",
    source: str = "research_hub",
    ts: Optional[str] = None,
) -> Optional[dict]:
    """单笔成交 → 交易记录行。价格、股数、说明都放在 meta，供页面「价格 / 明细」。"""
    if not isinstance(trade, dict):
        return None
    raw_side = str(trade.get("side") or "").strip().lower()
    side = _trade_side_for_log(raw_side)
    if side not in ("buy", "sell"):
        return None
    try:
        from core.paper.costs import fee_fields_from_trade, pnl_fields_from_trade
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        fee_fields_from_trade = None  # type: ignore
        pnl_fields_from_trade = None  # type: ignore
    t0_leg = raw_side.startswith("t0_")
    fee_meta: Dict[str, Any] = {}
    if fee_fields_from_trade is not None:
        try:
            fee_meta = fee_fields_from_trade(trade) or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            fee_meta = {}
    pnl_meta: Dict[str, Any] = {}
    if side == "sell" and pnl_fields_from_trade is not None:
        try:
            pnl_meta = pnl_fields_from_trade(trade) or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pnl_meta = {}
    name = trade.get("stock_name") or trade.get("stock_code") or "—"
    leg = "做T" if t0_leg or origin == "t0" else ""
    detail = (
        f"{leg}{'买入' if side == 'buy' else '卖出'} {name} "
        f"{trade.get('shares')}股 @ {trade.get('price')}"
    )
    when = str(ts or trade.get("ts") or "").strip() or _now_iso()
    return {
        "ts": when,
        "type": side,
        "type_label": OPERATION_LOG_TYPES.get(side, side),
        "detail": detail,
        "meta": {
            "stock_code": trade.get("stock_code"),
            "stock_name": trade.get("stock_name"),
            "shares": trade.get("shares"),
            "price": trade.get("price"),
            "amount": trade.get("amount") or trade.get("actual_cost"),
            "origin": "t0" if t0_leg or origin == "t0" else origin,
            "source": source,
            "note": trade.get("note"),
            "score": trade.get("score"),
            "y_oo_rank": trade.get("y_oo_rank"),
            "y_oo_rank_n": trade.get("y_oo_rank_n"),
            "y_tc_rank": trade.get("y_tc_rank"),
            "y_tc_rank_n": trade.get("y_tc_rank_n"),
            "sentiment_prior": bool(trade.get("sentiment_prior")),
            "market_prior": bool(trade.get("market_prior")),
            **fee_meta,
            **pnl_meta,
        },
    }


def _log_matches_trade(entry: dict, trade: dict) -> bool:
    side = _trade_side_for_log(str(trade.get("side") or ""))
    if str(entry.get("type") or "") != side:
        return False
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    if str(meta.get("stock_code") or "").strip() != str(trade.get("stock_code") or "").strip():
        return False
    try:
        log_shares = float(meta.get("shares"))
        trade_shares = float(trade.get("shares"))
    except (TypeError, ValueError):
        return False
    return abs(log_shares - trade_shares) <= 1e-6


def merge_unlogged_trade_legs(
    logs: Optional[list],
    trades: Optional[list],
    *,
    origin: str = "strategy",
    source: str = "open_fill",
) -> list:
    """读路径：成交已在 trades、流水里还没有对应买卖行时，补出带价格的记录。不改账本。"""
    entries = [x for x in (logs or []) if isinstance(x, dict)]
    used: set = set()
    extra: List[dict] = []
    for trade in trades or []:
        if not isinstance(trade, dict):
            continue
        matched = None
        for i, entry in enumerate(entries):
            if i in used:
                continue
            if _log_matches_trade(entry, trade):
                matched = i
                break
        if matched is not None:
            used.add(matched)
            continue
        leg_origin = str(trade.get("origin") or "").strip() or origin
        leg_source = str(trade.get("source") or "").strip() or source
        row = trade_leg_operation_entry(
            trade,
            origin=leg_origin,
            source=leg_source,
            ts=str(trade.get("ts") or "").strip() or None,
        )
        if row:
            extra.append(row)
    if not extra:
        return list(logs or [])
    merged = list(logs or []) + extra
    merged.sort(key=lambda e: str(e.get("ts") or "") if isinstance(e, dict) else "")
    return merged


def append_trade_legs_to_operation_log(
    paper: dict,
    sell_trades: Optional[List[Any]] = None,
    buy_trades: Optional[List[Any]] = None,
    *,
    origin: str = "cluster",
    source: str = "research_hub",
) -> None:
    """把调仓腿写入 operation_log，供交易执行页「交易记录」展示。"""
    for trade in list(sell_trades or []) + list(buy_trades or []):
        row = trade_leg_operation_entry(trade, origin=origin, source=source)
        if not row:
            continue
        append_operation_log(
            paper,
            str(row.get("type") or ""),
            detail=str(row.get("detail") or ""),
            meta=row.get("meta") if isinstance(row.get("meta"), dict) else {},
        )


