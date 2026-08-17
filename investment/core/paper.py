"""模拟账户账本（内部 canonical 名 paper；产品对外称「模拟」）。

非实盘。定量见 paper_sizing；成本见 paper_costs；行情经 core.ports。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterator, List, Optional, TypeVar

from core.paper_costs import resolve_cost_model  # noqa: F401 — re-export for paper_cycle / callers
from core.paper_sizing import (
    DEFAULT_SYNC_AMOUNT,
    DEFAULT_SYNC_LOT_SHARES,
    buy_codes_direct,
    plan_buy_codes,
)
from core.paths import PAPER_EXAMPLE_PATH, PAPER_PATH
from core.ports.market import quote_price
from core.ports.signal import build_signal_pool
from core.io_atomic import atomic_write_json
from core.file_lock import path_lock

DEFAULT_PAPER_PATH = PAPER_PATH
EXAMPLE_PATH = PAPER_EXAMPLE_PATH

# 账本列表上限：防长期运行 JSON 膨胀（与 operation_log/snapshots 对齐）
MAX_TRADES = 500
MAX_SNAPSHOTS = 120
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
    if isinstance(snaps, list) and len(snaps) > MAX_SNAPSHOTS:
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


def load_paper(path: Optional[str] = None) -> dict:
    p = path or DEFAULT_PAPER_PATH
    if not os.path.isfile(p):
        raise FileNotFoundError(f"模拟账户不存在: {p}（可先初始化）")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "cash" not in data:
        raise ValueError("paper.json 缺少 cash")
    data.pop("watchlist", None)  # 已废弃：名单只在 watching，仓位只在 holdings
    data.setdefault("holdings", [])
    data.setdefault("trades", [])
    data.setdefault("signal_log", [])
    data.setdefault("snapshots", [])
    data.setdefault("operation_log", [])
    data.setdefault("rules", {})
    data.setdefault("cost_model", "simple_cn")
    data.setdefault("cost_params", {})
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
    trim_paper_lists(data)
    with paper_write_lock(p):
        atomic_write_json(p, data)
    return p


def init_from_example(path: Optional[str] = None) -> str:
    p = path or DEFAULT_PAPER_PATH
    if os.path.isfile(p):
        raise FileExistsError(f"已存在: {p}")
    with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
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
                from core.watching_store import read_watching

                codes = [
                    str(c).strip()
                    for c in (read_watching().get("watchlist") or [])
                    if str(c).strip()
                ]
            except Exception:
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



# 盯市 / 手动与模拟成交：见 paper_exec
from core.paper_exec import (  # noqa: E402,F401
    mark_to_market,
    manual_buy,
    manual_sell,
    simulate_buys,
    simulate_sells,
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
    paper["snapshots"] = paper["snapshots"][-MAX_SNAPSHOTS:]


def capture_mark_snapshot(paper: dict) -> Dict[str, Any]:
    """改仓前盯市落点：把未实现涨跌从成交快照里拆开，避免曲线「一卖就跳」。"""
    summary = mark_to_market(paper)
    append_snapshot(paper, summary)
    return summary


OPERATION_LOG_TYPES = {
    "init": "初始化",
    "deposit": "注资",
    "withdraw": "减资",
    "reset": "回零",
    "buy": "买入",
    "sell": "卖出",
    "rebalance": "调仓",
    "cluster_pool_rebalance": "分池调仓",
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
        except Exception:
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


def append_trade_legs_to_operation_log(
    paper: dict,
    sell_trades: Optional[List[Any]] = None,
    buy_trades: Optional[List[Any]] = None,
    *,
    origin: str = "cluster",
    source: str = "research_hub",
) -> None:
    """把调仓腿写入 operation_log，供交易执行页「交易记录」展示。"""
    try:
        from core.paper_costs import fee_fields_from_trade, pnl_fields_from_trade
    except Exception:
        fee_fields_from_trade = None  # type: ignore
        pnl_fields_from_trade = None  # type: ignore

    for trade in list(sell_trades or []) + list(buy_trades or []):
        if not isinstance(trade, dict):
            continue
        side = str(trade.get("side") or "").strip().lower()
        if side not in ("buy", "sell"):
            continue
        fee_meta: Dict[str, Any] = {}
        if fee_fields_from_trade is not None:
            try:
                fee_meta = fee_fields_from_trade(trade) or {}
            except Exception:
                fee_meta = {}
        pnl_meta: Dict[str, Any] = {}
        if side == "sell" and pnl_fields_from_trade is not None:
            try:
                pnl_meta = pnl_fields_from_trade(trade) or {}
            except Exception:
                pnl_meta = {}
        name = trade.get("stock_name") or trade.get("stock_code") or "—"
        detail = (
            f"{'买入' if side == 'buy' else '卖出'} {name} "
            f"{trade.get('shares')}股 @ {trade.get('price')}"
        )
        append_operation_log(
            paper,
            side,
            detail=detail,
            meta={
                "stock_code": trade.get("stock_code"),
                "stock_name": trade.get("stock_name"),
                "shares": trade.get("shares"),
                "price": trade.get("price"),
                "amount": trade.get("amount") or trade.get("actual_cost"),
                "origin": origin,
                "source": source,
                "note": trade.get("note"),
                "score": trade.get("score"),
                **fee_meta,
                **pnl_meta,
            },
        )


# 调仓日循环：见 paper_cycle（保持 from core.paper import run_daily_cycle）
from core.paper_cycle import run_daily_cycle  # noqa: E402,F401
