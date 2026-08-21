"""对话轮次工具结果 → Web 右侧结果台 artifacts。"""


import logging

logger = logging.getLogger(__name__)
import json
from typing import Any, Dict, List, Optional

# 工具 → 右侧 Tab
TOOL_TAB: Dict[str, str] = {
    "quant": "quant",
    "backtest": "quant",
    "signal": "quant",
    "screen": "quant",
    "position": "follow",
    "advise": "follow",
    "quote": "reply",
    "compare": "reply",
    "kline": "reply",
    "fundamentals": "reply",
    "peer": "reply",
    "index": "reply",
    "news": "reply",
}

# quant task → 更细的 Tab 偏好
QUANT_TASK_TAB: Dict[str, str] = {
    "portfolio_bridge": "follow",
    "t0_backtest": "quant",
    "portfolio_backtest": "quant",
    "portfolio_neutral_compare": "quant",
    "daily_summary": "quant",
    "cross_section": "quant",
    "weight_suggest": "quant",
    "threshold_suggest": "quant",
    "factor_ols": "quant",
    "interpret": "quant",
}


def resolve_tab(tool: str, params: Optional[Dict[str, Any]] = None) -> str:
    params = params or {}
    if tool == "quant":
        task = str(params.get("task") or "").strip().lower()
        if task in QUANT_TASK_TAB:
            return QUANT_TASK_TAB[task]
    return TOOL_TAB.get(tool, "reply")


def _truncate_list(items: Any, n: int = 12) -> Any:
    if isinstance(items, list) and len(items) > n:
        return items[-n:]
    return items


def compact_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """缩小工具 JSON，避免 chat 响应过大。"""
    if not isinstance(data, dict):
        return {"success": False, "error": "invalid payload"}
    out: Dict[str, Any] = {}
    keep_scalar = (
        "success",
        "error",
        "task",
        "stock_code",
        "stock_name",
        "strategy",
        "name",
        "summary",
        "message",
        "winner",
        "source",
        "initialized",
        "count",
        "total_equity",
        "cash",
        "equity",
        "total_pnl_pct",
        "position_count",
        "t0_trade_days",
        "t0_pnl_total",
        "t0_pnl_with_exposure",
        "t0_cover_days",
        "cover_rate_pct",
        "uncover_days",
        "bar_count",
        "shares_end",
        "hold_mv_end",
        "trade_count",
        "data_source",
        "stance_label",
        "action",
        "score",
    )
    for k in keep_scalar:
        if k in data:
            out[k] = data[k]

    if "metrics" in data and isinstance(data["metrics"], dict):
        out["metrics"] = data["metrics"]
    if "params" in data and isinstance(data["params"], dict):
        # 只留少量参数摘要
        p = data["params"]
        out["params"] = {
            k: p[k]
            for k in ("common_dates", "stock_count", "top_k", "horizon_days", "min_score")
            if k in p
        }
    if "trades_sample" in data:
        out["trades_sample"] = _truncate_list(data["trades_sample"], 12)
    if "sim_trades" in data:
        out["sim_trades"] = _truncate_list(data["sim_trades"], 40)
        if data.get("sim_trade_count") is not None:
            out["sim_trade_count"] = data.get("sim_trade_count")
    if "equity_curve" in data:
        out["equity_curve"] = _truncate_list(data["equity_curve"], 60)
    if "equity_curve_tail" in data:
        out["equity_curve_tail"] = _truncate_list(data["equity_curve_tail"], 60)
    if "days" in data:
        out["days"] = _truncate_list(data["days"], 15)
    if "loaded_stocks" in data and isinstance(data["loaded_stocks"], list):
        out["loaded_stocks"] = data["loaded_stocks"][:20]
    if "advice" in data and isinstance(data["advice"], list):
        out["advice"] = data["advice"][:15]
    if "holdings" in data and isinstance(data["holdings"], list):
        out["holdings"] = data["holdings"][:15]
    if "picks" in data and isinstance(data["picks"], list):
        out["picks"] = data["picks"][:15]
    if "rows" in data and isinstance(data["rows"], list):
        out["rows"] = data["rows"][:20]
    if "quotes" in data:
        out["quotes"] = data["quotes"]
    if "price" in data:
        out["price"] = data["price"]
    if "change_pct" in data:
        out["change_pct"] = data["change_pct"]
    if "delta" in data:
        out["delta"] = data["delta"]
    if "paper" in data and isinstance(data["paper"], dict):
        out["paper"] = {
            k: data["paper"][k]
            for k in ("equity", "total_pnl_pct", "position_count", "initialized")
            if k in data["paper"]
        }
    if "portfolio_backtest_summary" in data and isinstance(
        data["portfolio_backtest_summary"], dict
    ):
        out["portfolio_backtest_summary"] = data["portfolio_backtest_summary"]
    if "cross_section" in data and isinstance(data["cross_section"], dict):
        cs = data["cross_section"]
        out["cross_section"] = {
            "success": cs.get("success"),
            "picks": _truncate_list(cs.get("picks") or [], 10),
        }

    # 兜底：若几乎为空，保留原 dict 的浅拷贝（截断长 list）
    if len(out) <= 1 and data:
        for k, v in list(data.items())[:30]:
            if isinstance(v, list):
                out[k] = _truncate_list(v, 10)
            elif isinstance(v, (str, int, float, bool, type(None))) or isinstance(v, dict) and len(json.dumps(v, ensure_ascii=False)) < 4000:
                out[k] = v
    return out


def build_artifact(
    tool: str,
    params: Optional[Dict[str, Any]],
    result_raw: str,
) -> Dict[str, Any]:
    params = params if isinstance(params, dict) else {}
    try:
        parsed = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in artifacts.py", exc_info=True)
        parsed = {"success": False, "error": "无法解析工具结果", "raw": str(result_raw)[:500]}
    if not isinstance(parsed, dict):
        parsed = {"success": False, "error": "工具结果非对象", "value": str(parsed)[:500]}

    tab = resolve_tab(tool, params)
    data = compact_payload(parsed)
    success = parsed.get("success")
    if success is None:
        success = "error" not in parsed
    else:
        success = bool(success)
    summary_parts = [tool]
    if tool == "quant" and params.get("task"):
        summary_parts.append(str(params["task"]))
    if parsed.get("stock_code") or parsed.get("stock_name"):
        summary_parts.append(str(parsed.get("stock_name") or parsed.get("stock_code")))
    if isinstance(parsed.get("metrics"), dict):
        m = parsed["metrics"]
        if m.get("total_return_pct") is not None:
            summary_parts.append(f"累计{m.get('total_return_pct')}%")
    if parsed.get("price") is not None:
        summary_parts.append(f"价{parsed.get('price')}")
    if not success:
        summary_parts.append(str(parsed.get("error") or "失败")[:80])

    return {
        "tool": tool,
        "params": {k: params[k] for k in list(params)[:12]},
        "tab": tab,
        "success": success,
        "summary": " · ".join(summary_parts),
        "data": data,
    }


def primary_tab(artifacts: List[Dict[str, Any]]) -> str:
    """优先最后一个成功 artifact 的 tab。"""
    if not artifacts:
        return "reply"
    for art in reversed(artifacts):
        if art.get("success") and art.get("tab"):
            return str(art["tab"])
    return str(artifacts[-1].get("tab") or "reply")
