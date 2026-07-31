"""持仓规则引擎：阈值可配置；默认读模拟账户 paper，亦可传临时 holdings。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


from core.numbers import to_float as _to_float
from core.paths import PAPER_PATH, POSITION_RULES_PATH

DEFAULT_PAPER = PAPER_PATH
DEFAULT_RULES = POSITION_RULES_PATH

DEFAULT_RULE_VALUES = {
    "concentration_high": 35,
    "concentration_warn": 25,
    "take_profit_pnl": 8,
    "take_profit_day_change": -2,
    "trail_pnl": 5,
    "stop_loss_pnl": -8,
    "stop_loss_day_change": -2,
    "reduce_pnl": -5,
    "reduce_day_change": -3,
    "short_horizon_day_abs": 4,
    "stop_buffer_pct": 3,
}


def load_rules(path: Optional[str] = None) -> dict:
    path = path or DEFAULT_RULES
    if not os.path.isabs(path):
        from core.paths import ROOT_DIR

        path = os.path.join(ROOT_DIR, path)
    rules = dict(DEFAULT_RULE_VALUES)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if isinstance(data, dict):
            for k, v in data.items():
                if k in rules and _to_float(v) is not None:
                    rules[k] = _to_float(v)
    rules["_path"] = path if os.path.isfile(path) else None
    return rules


def _resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    from core.paths import ROOT_DIR

    return os.path.join(ROOT_DIR, path)


def load_paper_holdings(path: Optional[str] = None) -> dict:
    """读模拟账户 paper.json 的现金与持仓（对话 position 默认）。"""
    from core.paper import load_paper

    path = _resolve_path(path or DEFAULT_PAPER)
    data = load_paper(path)
    holdings = data.get("holdings") or []
    if not isinstance(holdings, list):
        raise ValueError("paper holdings 应为数组")
    return {
        "path": path,
        "cash": _to_float(data.get("cash")) or 0.0,
        "holdings": holdings,
        "source": "paper",
    }


def resolve_holdings_book(params: dict) -> dict:
    """解析持仓账本：inline holdings → 默认 / 显式 paper_path。"""
    inline = params.get("holdings")
    if inline is not None:
        if not isinstance(inline, list):
            raise ValueError("holdings 应为数组")
        return {
            "path": "(inline)",
            "cash": _to_float(params.get("cash")) or 0.0,
            "holdings": inline,
            "source": "inline",
        }

    return load_paper_holdings(params.get("paper_path"))


def evaluate_holding(
    *,
    stock_code: str,
    stock_name: str,
    shares: float,
    cost: float,
    price: float,
    change_pct: Optional[float],
    market_value: float,
    total_equity: float,
    horizon_days: int = 3,
    rules: Optional[dict] = None,
) -> dict:
    """对单票输出动作与理由。"""
    r = rules or DEFAULT_RULE_VALUES
    pnl_pct = ((price / cost) - 1.0) * 100.0 if cost else 0.0
    weight = (market_value / total_equity * 100.0) if total_equity > 0 else 0.0
    change_pct = change_pct if change_pct is not None else 0.0

    action = "观望"
    urgency = "低"
    reasons: List[str] = []
    suggested_weight_range = None
    risk_notes: List[str] = []

    conc_high = r["concentration_high"]
    conc_warn = r["concentration_warn"]

    if weight >= conc_high:
        action = "降集中度"
        urgency = "高"
        reasons.append(f"单票仓位约 {weight:.1f}%，集中度偏高（阈值≥{conc_high}%）")
        suggested_weight_range = [10, 20]
    elif weight >= conc_warn:
        reasons.append(f"单票仓位约 {weight:.1f}%，注意控制集中度（阈值≥{conc_warn}%）")
        if action == "观望":
            action = "控制仓位"

    if pnl_pct >= r["take_profit_pnl"] and change_pct <= r["take_profit_day_change"]:
        action = "减仓锁定"
        urgency = "中"
        reasons.append(
            f"浮盈约 {pnl_pct:+.1f}% 且当日 {change_pct:+.1f}%，可考虑部分兑现"
        )
        suggested_weight_range = suggested_weight_range or [max(5, weight * 0.5), weight * 0.7]
    elif pnl_pct >= r["trail_pnl"] and change_pct >= 0:
        if action in ("观望", "控制仓位"):
            action = "持有并上移止盈"
        reasons.append(f"浮盈约 {pnl_pct:+.1f}%，短线动能尚未明显转弱")
        risk_notes.append(
            f"建议将止盈参考上移至成本上方约 {max(3, pnl_pct * 0.5):.0f}%"
        )

    if pnl_pct <= r["stop_loss_pnl"] and change_pct <= r["stop_loss_day_change"]:
        action = "止损参考"
        urgency = "高"
        reasons.append(f"浮亏约 {pnl_pct:+.1f}% 且当日续跌 {change_pct:+.1f}%")
        suggested_weight_range = [0, max(5, weight * 0.3)]
    elif pnl_pct <= r["reduce_pnl"] and change_pct <= r["reduce_day_change"]:
        action = "减仓观察"
        urgency = "中"
        reasons.append(f"浮亏约 {pnl_pct:+.1f}% 且当日跌幅 {change_pct:+.1f}%")
        suggested_weight_range = suggested_weight_range or [0, weight * 0.6]

    if horizon_days <= 2 and abs(change_pct) >= r["short_horizon_day_abs"]:
        risk_notes.append("1～2 天窗口波动加大，优先风控而非追涨杀跌")

    if not reasons:
        reasons.append(f"浮盈亏 {pnl_pct:+.1f}%，当日 {change_pct:+.1f}%，暂无触发强规则")

    if suggested_weight_range is None:
        lo = max(0, min(weight, 15) * 0.8)
        hi = min(25, max(weight, 5))
        suggested_weight_range = [round(lo, 1), round(hi, 1)]
    else:
        suggested_weight_range = [
            round(float(suggested_weight_range[0]), 1),
            round(float(suggested_weight_range[1]), 1),
        ]

    buf = r["stop_buffer_pct"] / 100.0
    stop_ref = round(price * (1 - buf), 2)
    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "shares": shares,
        "cost": cost,
        "price": price,
        "pnl_pct": round(pnl_pct, 2),
        "change_pct": round(change_pct, 2),
        "weight_pct": round(weight, 2),
        "action": action,
        "urgency": urgency,
        "reasons": reasons,
        "suggested_weight_pct_range": suggested_weight_range,
        "invalidation": [f"跌破参考位约 {stop_ref}（约 -{r['stop_buffer_pct']}%）需重新评估"],
        "risk_notes": risk_notes,
    }


class PositionEngine:
    def advise(self, params: dict, quote_fn=None) -> dict:
        from core.ports.market import query_quote

        quote_fn = quote_fn or query_quote
        horizon = max(1, min(int(params.get("horizon_days") or 3), 3))
        include_stance = bool(params.get("include_stance", False))
        full_stance = bool(params.get("include_full_stance", False))
        rules = load_rules(params.get("rules_path"))

        try:
            portfolio = resolve_holdings_book(params or {})
        except Exception as e:
            return {"success": False, "error": str(e)}

        holdings = portfolio["holdings"]
        source = portfolio.get("source") or "paper"
        if not holdings:
            empty_hint = {
                "paper": "模拟持仓为空，可先在观察页建仓，或在参数中传入 holdings",
                "inline": "传入的 holdings 为空",
            }.get(source, "持仓为空")
            return {
                "success": True,
                "count": 0,
                "advice": [],
                "source": source,
                "portfolio_path": portfolio["path"],
                "message": empty_hint,
                "note": "市场有风险，不保证收益，不代客下单。",
            }

        enriched = []
        failures = []
        for h in holdings:
            code = str(h.get("stock_code") or h.get("code") or "").strip()
            if not code:
                failures.append({"error": "缺少 stock_code"})
                continue
            shares = _to_float(h.get("shares") or h.get("quantity")) or 0.0
            cost = _to_float(h.get("cost") or h.get("cost_price")) or 0.0
            if shares <= 0 or cost <= 0:
                failures.append({"stock_code": code, "error": "shares/cost 无效"})
                continue

            q = quote_fn(code)
            if not q.get("success"):
                failures.append({"stock_code": code, "error": q.get("error", "行情失败")})
                continue
            price = _to_float(q.get("price_raw"))
            if price is None or price <= 0:
                failures.append({"stock_code": code, "error": "无法解析现价"})
                continue
            name = q.get("stock_name") or h.get("stock_name") or code
            change = _to_float(q.get("change_raw"))
            mv = price * shares
            enriched.append(
                {
                    "stock_code": q.get("stock_code") or code,
                    "stock_name": name,
                    "shares": shares,
                    "cost": cost,
                    "price": price,
                    "change_pct": change,
                    "market_value": mv,
                    "quote": q,
                }
            )

        if not enriched:
            return {
                "success": False,
                "error": "所有持仓均无法获取有效行情",
                "failures": failures,
            }

        cash = portfolio["cash"]
        total_equity = cash + sum(x["market_value"] for x in enriched)
        advice = []
        for x in enriched:
            row = evaluate_holding(
                stock_code=x["stock_code"],
                stock_name=x["stock_name"],
                shares=x["shares"],
                cost=x["cost"],
                price=x["price"],
                change_pct=x["change_pct"],
                market_value=x["market_value"],
                total_equity=total_equity,
                horizon_days=horizon,
                rules=rules,
            )
            if include_stance:
                from core.position import attach_stance_to_advice

                row = attach_stance_to_advice(
                    row,
                    quote=x["quote"],
                    horizon_days=horizon,
                    full_context=full_stance,
                )
            advice.append(row)

        top = max(advice, key=lambda a: a["weight_pct"]) if advice else None
        summary_lines = [
            f"来源：{'模拟账户' if source == 'paper' else '临时持仓'}。",
            f"总权益约 {total_equity:.0f}（含现金 {cash:.0f}），持仓 {len(advice)} 只。",
            f"展望窗口约 {horizon} 天。",
            f"规则文件: {rules.get('_path') or '内置默认'}",
        ]
        if top and top["weight_pct"] >= rules["concentration_warn"]:
            summary_lines.append(
                f"集中度提示：{top['stock_name']} 约占 {top['weight_pct']:.1f}%，建议优先处理。"
            )

        return {
            "success": True,
            "horizon_days": horizon,
            "source": source,
            "portfolio_path": portfolio["path"],
            "rules_path": rules.get("_path"),
            "total_equity": round(total_equity, 2),
            "cash": cash,
            "count": len(advice),
            "advice": advice,
            "summary": "\n".join(summary_lines),
            "failures": failures,
            "note": "以上为规则化持仓建议依据，市场有风险，不保证收益，不代客下单。",
        }
