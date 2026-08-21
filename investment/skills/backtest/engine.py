"""backtest Skill：拉取日线并对 signal 规则做 walk-forward 回测。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.backtest.strategies import get_strategy, list_strategies, merge_strategy_params, run_strategy_backtest
from core.backtest.topk_backtest import aggregate_stock_backtests
from core.data_service import get_bars, get_quote
from core.ports.market import (
    default_benchmark,
    fetch_index_bars,
    resolve_market_code,
)
from core.strategy import get_strategy_spec

logger = logging.getLogger(__name__)


class BacktestEngine:
    def _resolve_benchmark(
        self,
        params: dict,
        market: Optional[str],
    ) -> tuple:
        bench = params.get("benchmark")
        if bench is None or bench == "":
            bench = "auto"
        bench = str(bench).strip()
        if bench.lower() in ("none", "off", "false", "0"):
            return [], None
        if bench.lower() in ("auto", "default"):
            bench = default_benchmark(market)
        bars, label = fetch_index_bars(bench, limit=int(params.get("lookback_days") or 120) + 35)
        return bars, label or bench

    def run(self, params: dict) -> dict:
        strategy = (params.get("strategy") or "short").strip()
        try:
            get_strategy(strategy)
            strategy_spec = get_strategy_spec(strategy)
        except KeyError as e:
            return {
                "success": False,
                "error": str(e),
                "available_strategies": [s["name"] for s in list_strategies()],
            }

        codes = params.get("stock_codes") or []
        if isinstance(codes, str):
            codes = [c.strip() for c in codes.replace("，", ",").split(",") if c.strip()]
        if not codes:
            codes = ["茅台"]

        lookback = int(params.get("lookback_days") or 120)
        lookback = max(40, min(lookback, 500))

        overrides = {
            "horizon_days": params.get("horizon_days"),
            "min_score": params.get("min_score"),
            "data_mode": params.get("data_mode"),
            "apply_costs": params.get("apply_costs"),
        }

        per_stock: List[dict] = []
        failures: List[dict] = []

        for raw in codes[:5]:
            quote = get_quote(str(raw).strip())
            name = quote.get("stock_name") if quote.get("success") else str(raw)
            code = quote.get("stock_code") if quote.get("success") else str(raw)
            market, _ = resolve_market_code(raw)

            pack = get_bars(raw, limit=lookback + 35, reject_quote_fallback=True)
            bars = list(pack.get("bars") or [])
            data_source = str(pack.get("data_source") or "empty")
            if not bars and quote.get("success"):
                pack = get_bars(code, limit=lookback + 35, reject_quote_fallback=True)
                bars = list(pack.get("bars") or [])
                data_source = str(pack.get("data_source") or "empty")

            if not bars:
                failures.append(
                    {
                        "stock_code": code,
                        "stock_name": name,
                        "error": "无法获取日线，回测跳过",
                        "quality": pack.get("quality"),
                        "production_ok": pack.get("production_ok"),
                    }
                )
                continue

            index_bars, index_label = self._resolve_benchmark(params, market)
            strat_params = merge_strategy_params(strategy, overrides)

            result = run_strategy_backtest(
                bars,
                strategy,
                overrides=overrides,
                index_bars=index_bars or None,
                index_label=index_label,
            )
            entry: Dict[str, Any] = {
                "stock_code": code,
                "stock_name": name,
                "data_source": data_source,
                "lookback_bars": len(bars),
                "strategy_params": strat_params,
                "quality": pack.get("quality"),
                "production_ok": pack.get("production_ok"),
                "fallback": pack.get("fallback"),
                "adjust": pack.get("adjust"),
            }
            entry.update(result)
            per_stock.append(entry)

        if not per_stock:
            return {
                "success": False,
                "error": "所有标的均无可用日线，无法回测",
                "failures": failures,
            }

        total_trades = 0
        summary_lines = []
        for row in per_stock:
            if not row.get("success"):
                summary_lines.append(
                    f"- {row.get('stock_name')}({row.get('stock_code')}): 失败 — {row.get('error')}"
                )
                continue
            m = row.get("metrics") or {}
            b = row.get("benchmark") or {}
            total_trades += int(m.get("trade_count") or 0)
            excess = b.get("excess_vs_buy_hold_pct")
            excess_s = f", 超额(vs持有) {excess}%" if excess is not None else ""
            idx_excess = b.get("excess_avg_vs_index_pct")
            idx_s = ""
            if idx_excess is not None and b.get("index_label"):
                idx_s = f", 超额(vs{b.get('index_label')}) {idx_excess}%"
            summary_lines.append(
                f"- {row.get('stock_name')}({row.get('stock_code')}): "
                f"交易 {m.get('trade_count')} 次, 胜率 {m.get('win_rate_pct')}%, "
                f"均收益 {m.get('avg_return_pct')}%, 累计 {m.get('total_return_pct')}%"
                f"{excess_s}{idx_s}"
            )
            buckets = row.get("score_buckets") or []
            if buckets:
                parts = [
                    f"{x['bucket']}:{x.get('avg_return_pct')}%"
                    for x in buckets[:3]
                ]
                summary_lines.append(f"  分层: {', '.join(parts)}")

        portfolio = aggregate_stock_backtests(per_stock)
        merged_params = merge_strategy_params(strategy, overrides)
        cost_model = strategy_spec.get("cost_model") or "simple_cn"
        if merged_params.get("apply_costs") is False:
            cost_model = "zero"

        manifest = None
        try:
            from core.run_manifest import build_run_manifest, write_run_manifest

            manifest = build_run_manifest(
                kind="backtest",
                strategy_id=strategy_spec.get("strategy_id") or strategy,
                strategy_version=strategy_spec.get("version"),
                cost_model=cost_model,
                rules=merged_params,
                extra={
                    "lookback_days": lookback,
                    "stock_count": len(per_stock),
                    "codes": [r.get("stock_code") for r in per_stock],
                    "data_sources": list(
                        {r.get("data_source") for r in per_stock if r.get("data_source")}
                    ),
                },
            )
            manifest["path"] = write_run_manifest(manifest)
        except Exception:
            logger.exception('unexpected error in run')

        return {
            "success": True,
            "strategy": strategy,
            "strategy_id": strategy_spec.get("strategy_id") or strategy,
            "strategy_version": strategy_spec.get("version"),
            "strategy_label": get_strategy(strategy).get("label"),
            "params": {
                "lookback_days": lookback,
                "benchmark": params.get("benchmark") or "auto",
                "stock_count": len(per_stock),
                **merged_params,
            },
            "available_strategies": list_strategies(),
            "results": per_stock,
            "failures": failures,
            "aggregate": {
                "total_trades": total_trades,
                "stocks_tested": len(per_stock),
                "portfolio": portfolio,
            },
            "summary": "\n".join(summary_lines),
            "manifest": manifest,
            "note": (
                "研究用回测，未含完整涨跌停/T+1；LLM 解读须引用 metrics/benchmark 数字，禁止编造。"
                "市场有风险，不保证收益，不代客下单。"
            ),
        }
