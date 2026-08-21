"""仪表盘：板块热力 · 信号告警 · 资产配置。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from fastapi import HTTPException

logger = logging.getLogger(__name__)

from web import deps
from web.dashboard.paper_helpers import (
    _holding_market_value,
    _holding_sector,
    _holdings_from_paper,
    _load_raw_paper,
)

def _build_sector_heatmap() -> Dict[str, Any]:
    """板块热力图。优先用持仓聚合，退化到行业默认列表。含市值/涨跌/个股数。"""
    try:
        paper = _load_raw_paper()
        holdings = _holdings_from_paper(paper)
        sectors_map: Dict[str, Dict[str, Any]] = {}
        try:
            from core.portfolio_optimize import load_sector_map

            smap = load_sector_map()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
            smap = {}

        for h in holdings:
            sector = _holding_sector(h, smap)
            value = _holding_market_value(h)
            cost = h.get("cost_value")
            if cost is None:
                try:
                    shares = float(h.get("shares") or 0)
                    unit_cost = float(h.get("cost") or 0)
                    cost = shares * unit_cost
                except (TypeError, ValueError):
                    cost = value
            try:
                cost = float(cost or 0)
            except (TypeError, ValueError):
                cost = 0.0
            change_pct = ((value / cost - 1) * 100) if cost and cost > 0 else 0
            volume = h.get("volume") or h.get("turnover") or 0
            if sector not in sectors_map:
                sectors_map[sector] = {"name": sector, "value": 0, "cost": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0}
            sectors_map[sector]["value"] += value
            sectors_map[sector]["cost"] += cost
            sectors_map[sector]["count"] += 1
            sectors_map[sector]["volume"] += volume
            if change_pct > 0:
                sectors_map[sector]["up_count"] += 1
            elif change_pct < 0:
                sectors_map[sector]["down_count"] += 1

        sectors = []
        for name, data in sectors_map.items():
            change = ((data["value"] / data["cost"] - 1) * 100) if data["cost"] > 0 else 0
            total_count = data["count"]
            up_ratio = (data["up_count"] / total_count * 100) if total_count > 0 else 0
            sectors.append({
                "name": name,
                "change_pct": round(change, 2),
                "value": round(data["value"], 2),
                "count": data["count"],
                "volume": round(data["volume"], 2),
                "up_count": data["up_count"],
                "down_count": data["down_count"],
                "up_ratio": round(up_ratio, 1),
            })

        if not sectors:
            sectors = [
                {"name": "电子", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "电力设备", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "医疗生物", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "计算机", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "通信", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "金融", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "消费", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "化工", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "机械", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "新能源", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "军工", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
                {"name": "其他", "change_pct": 0, "value": 0, "count": 0, "volume": 0, "up_count": 0, "down_count": 0, "up_ratio": 0},
            ]

        sectors.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        return {"ok": True, "sectors": sectors}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _build_signals(limit: int = 20) -> Dict[str, Any]:
    """最新信号 / 告警。优先 signal_log，其次 operation_log 成交/风控。"""
    try:
        paper = _load_raw_paper()
        signals: List[Dict[str, Any]] = []
        limit = max(1, min(int(limit or 20), 50))
        holdings = _holdings_from_paper(paper)
        name_by_code: Dict[str, str] = {}
        for h in holdings:
            c = str(h.get("code") or h.get("stock_code") or "").strip()
            n = str(h.get("name") or h.get("stock_name") or "").strip()
            if c and n:
                name_by_code[c] = n
        try:
            from core.watching_store import read_watching, watchlist_names_for

            uni = read_watching()
            codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
            names = watchlist_names_for(uni) or []
            for i, c in enumerate(codes):
                if not c or c in name_by_code:
                    continue
                n = str(names[i]).strip() if i < len(names) else ""
                if n and n != c:
                    name_by_code[c] = n
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
            pass

        def _with_name(row: Dict[str, Any]) -> Dict[str, Any]:
            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip() or name_by_code.get(code) or ""
            if name:
                row["name"] = name
            return row

        # From signal_log (observation_pool scans)
        for entry in reversed(paper.get("signal_log") or []):
            if not isinstance(entry, dict) or not entry.get("success"):
                continue
            ts = entry.get("ts") or entry.get("time")
            pool = entry.get("observation_pool") or []
            for item in pool[:8]:
                if not isinstance(item, dict):
                    continue
                score = item.get("predicted_score")
                if score is None:
                    score = item.get("score")
                code = item.get("stock_code") or item.get("code")
                signals.append(_with_name({
                    "code": code,
                    "name": item.get("stock_name") or item.get("name"),
                    "direction": "bullish" if (score or 0) > 0 else "bearish" if (score or 0) < 0 else "—",
                    "score": score,
                    "time": ts,
                    "type": "signal",
                    "sector": item.get("sector"),
                }))
            if len(signals) >= limit:
                break

        # From operation_log (trades / risk / settings)
        # append_operation_log 把股票字段放在 meta，不在顶层
        if len(signals) < limit:
            op_log = paper.get("operation_log") or []
            try:
                from services.paper_account import PaperAccountMixin

                op_log = PaperAccountMixin._operation_log_for_ui(
                    deps.paper, paper, limit=max(limit * 2, 40)
                )
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
                op_log = (paper.get("operation_log") or [])[-max(limit * 2, 40) :]
            trade_types = {
                "buy",
                "sell",
                "rebalance",
                "cluster_pool_rebalance",
                "risk_block",
                "signal",
                "alert",
                "risk_budget",
            }
            for entry in reversed(op_log):
                if not isinstance(entry, dict):
                    continue
                et = entry.get("type") or entry.get("action") or ""
                if et not in trade_types:
                    continue
                meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
                code = (
                    entry.get("code")
                    or entry.get("stock_code")
                    or meta.get("stock_code")
                    or meta.get("code")
                )
                name = (
                    entry.get("name")
                    or entry.get("stock_name")
                    or meta.get("stock_name")
                    or meta.get("name")
                )
                score = entry.get("score")
                if score is None:
                    score = entry.get("predicted_score")
                if score is None:
                    score = meta.get("score")
                if score is None:
                    score = meta.get("predicted_score")
                # 汇总行（分池调仓摘要）无单票 code，跳过以免「—」占位
                if not code:
                    continue
                signals.append(_with_name({
                    "code": code,
                    "name": name,
                    "direction": entry.get("direction")
                    or entry.get("side")
                    or meta.get("side")
                    or et,
                    "score": score,
                    "time": entry.get("ts") or entry.get("time"),
                    "type": et,
                }))
                if len(signals) >= limit:
                    break

        # Fallback: holdings with stored scores / sentiment
        if not signals:
            for h in holdings[:limit]:
                sentiment = h.get("sentiment") or {}
                label = sentiment.get("label") or ""
                score = h.get("predicted_score_blend")
                if score is None:
                    score = h.get("decision_score")
                if score is None:
                    score = h.get("score")
                if score is None:
                    score = h.get("predicted_score")
                if label or score is not None:
                    direction = (
                        "bearish"
                        if label == "bearish"
                        else "bullish"
                        if label == "bullish"
                        else "—"
                    )
                    signals.append(_with_name({
                        "code": h.get("code") or h.get("stock_code"),
                        "name": h.get("name") or h.get("stock_name"),
                        "direction": direction,
                        "score": score,
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "type": "holding",
                    }))

        signals = signals[:limit]
        return {"ok": True, "signals": signals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _build_allocation() -> Dict[str, Any]:
    """资产配置（按板块汇总市值）。"""
    try:
        paper = _load_raw_paper()
        holdings = _holdings_from_paper(paper)
        sectors_map: Dict[str, float] = {}
        total_value = 0.0
        try:
            from core.portfolio_optimize import load_sector_map

            smap = load_sector_map()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_dashboard.py", exc_info=True)
            smap = {}

        for h in holdings:
            sector = _holding_sector(h, smap)
            value = _holding_market_value(h)
            sectors_map[sector] = sectors_map.get(sector, 0.0) + value
            total_value += value

        try:
            cash = float(paper.get("cash") or paper.get("available_cash") or 0)
        except (TypeError, ValueError):
            cash = 0.0
        if cash > 0:
            sectors_map["现金"] = sectors_map.get("现金", 0.0) + cash
            total_value += cash

        sectors = [
            {
                "name": name,
                "value": round(val, 2),
                "pct": round(val / total_value * 100, 2) if total_value > 0 else 0,
            }
            for name, val in sorted(sectors_map.items(), key=lambda x: -x[1])
        ]

        if not sectors:
            sectors = [{"name": "暂无持仓", "value": 0, "pct": 0}]

        return {
            "ok": True,
            "sectors": sectors,
            "total_value": round(total_value, 2),
            "holdings_count": len(holdings),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

