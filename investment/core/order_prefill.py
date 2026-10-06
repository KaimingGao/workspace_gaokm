"""非交易指令通道：订单预填导出（人工到券商 App 确认）。"""


import logging

logger = logging.getLogger(__name__)
import csv
import io
import time
from typing import Any, Dict, List

DISCLAIMER = (
    "非交易指令通道：本文件仅为预填建议，不提交券商、不划拨资金。"
    "最终下单须在官方券商 App 内由人工二次确认。"
    "市场有风险，不保证收益，不代客下单。"
)


def build_prefills_from_decisions(
    decisions: List[Dict[str, Any]],
    *,
    default_side: str = "buy",
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for d in decisions or []:
        label = str(d.get("stance_label") or "")
        side = default_side
        if any(x in label for x in ("减仓", "卖出", "止损", "回避")):
            side = "sell"
        elif any(x in label for x in ("观望", "等待", "不足")):
            continue
        rows.append(
            {
                "stock_code": d.get("stock_code"),
                "stock_name": d.get("stock_name"),
                "side": side,
                "stance_label": label,
                "qty_hint": "",
                "price_hint": "",
                "decision_id": d.get("id"),
                "note": "预填；数量/价格请人工填写",
            }
        )
    return rows


def export_prefill_bundle(
    rows: List[Dict[str, Any]],
    *,
    fmt: str = "json",
) -> Dict[str, Any]:
    payload = {
        "ok": True,
        "channel": "non_trading_prefill",
        "ts": time.time(),
        "disclaimer": DISCLAIMER,
        "count": len(rows),
        "orders": rows,
    }
    if fmt == "csv":
        buf = io.StringIO()
        fields = [
            "stock_code",
            "stock_name",
            "side",
            "stance_label",
            "qty_hint",
            "price_hint",
            "decision_id",
            "note",
        ]
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        payload["csv"] = buf.getvalue()
        payload["format"] = "csv"
    else:
        payload["format"] = "json"
    return payload


def prefill_from_recent_decisions(
    *,
    limit: int = 10,
    fmt: str = "json",
) -> Dict[str, Any]:
    from core.decision_record import list_decisions

    items = list_decisions(limit=limit).get("items") or []
    rows = build_prefills_from_decisions(items)
    return export_prefill_bundle(rows, fmt=fmt)
