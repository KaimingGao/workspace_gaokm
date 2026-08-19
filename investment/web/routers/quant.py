"""量化研究台 API。

产品动作（URL 不变）：
  策略 — /api/signal/config/* · /api/quant/config
  历史验证 — /api/watching/* · portfolio-backtest · cross-section · neutral-compare
  前瞻验证 — /api/paper/* · /api/quant/t0-backtest
  联动摘要 — /api/portfolio/quant-bridge（paper 持仓，只读）
映射：GET /api/quant/actions
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web import deps
from web.schemas import QuantInterpretRequest, QuantReportDeleteRequest, QuantReportRequest

router = APIRouter(tags=["quant"])


class PortfolioBacktestExportBody(BaseModel):
    """R4.4 · 回溯页一键导出机构报告。"""

    result: Dict[str, Any]
    format: str = "markdown"


@router.get("/api/quant/last")
def quant_last():
    return deps.quant.load_last_daily()


@router.post("/api/quant/report")
def quant_report(body: QuantReportRequest):
    try:
        report = deps.quant.build_daily_report(
            body.code,
            include_cross_section=body.include_cross_section,
            include_portfolio_backtest=body.include_portfolio_backtest,
        )
        if body.save:
            deps.quant.save_daily_report(report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return report


@router.get("/api/quant/strategies")
def quant_strategies():
    return deps.quant.list_strategies()


@router.post("/api/quant/interpret")
def quant_interpret(body: QuantInterpretRequest):
    try:
        report = None
        if body.save_before_interpret:
            report = deps.quant.build_daily_report(body.code, include_portfolio_backtest=False)
            deps.quant.save_daily_report(report)
        elif body.use_saved:
            saved = deps.quant.load_last_daily()
            if not saved.get("empty"):
                report = saved
        result = deps.quant.interpret_report(
            report,
            use_saved=False if report else body.use_saved,
            offline=body.offline,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not result.get("success"):
        raise HTTPException(status_code=503, detail=result.get("error") or "解读失败")
    return result


@router.get("/api/quant/export")
def quant_export(format: str = "markdown", use_saved: bool = True):
    fmt = (format or "markdown").strip().lower()
    if fmt not in ("markdown", "html"):
        raise HTTPException(status_code=400, detail="仅支持 format=markdown|html")
    result = deps.quant.export_report(use_saved=use_saved, fmt=fmt)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error") or "导出失败")
    return result


@router.post("/api/quant/export/backtest")
def quant_export_backtest(body: PortfolioBacktestExportBody):
    """R4.4 · 导出单次 Top-K 回测机构报告（与页内块序对齐）。"""
    fmt = (body.format or "markdown").strip().lower()
    if fmt not in ("markdown", "html"):
        raise HTTPException(status_code=400, detail="仅支持 format=markdown|html")
    result = deps.quant.export_portfolio_backtest_report(body.result, fmt=fmt)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error") or "导出失败")
    return result


@router.get("/api/quant/export/summary")
def quant_export_summary(use_saved: bool = True):
    result = deps.quant.export_executive_summary(use_saved=use_saved)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error") or "无摘要")
    return result


@router.get("/api/quant/reports")
def quant_reports(limit: int = 20):
    limit = max(1, min(int(limit or 20), 100))
    return deps.quant.list_report_archive(limit=limit)


@router.post("/api/quant/reports/delete")
def quant_reports_delete(body: QuantReportDeleteRequest):
    """删除指定日（或批量）归档日报 quant_daily_*.{md,html}。"""
    try:
        out = deps.quant.delete_report_archive(
            stamp=body.stamp,
            date=body.date,
            stamps=body.stamps,
            dates=body.dates,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not out.get("success"):
        raise HTTPException(status_code=404, detail=out.get("error") or "删除失败")
    return out


@router.get("/api/quant/reports/{filename}")
def quant_report_file(filename: str):
    result = deps.quant.read_report_archive(filename)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error") or "未找到报告")
    return result


@router.get("/api/paper/quant-bridge")
def paper_quant_bridge(include_stance: bool = False):
    """模拟持仓联动摘要（canonical；持仓取自 paper）。"""
    try:
        out = deps.quant.build_portfolio_bridge(include_stance=include_stance)
        if isinstance(out, dict):
            out = dict(out)
            out["canonical"] = True
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/portfolio/quant-bridge")
def portfolio_quant_bridge(include_stance: bool = False):
    """已弃用别名 → 请用 ``GET /api/paper/quant-bridge``（对照仓已下线）。"""
    try:
        out = deps.quant.build_portfolio_bridge(include_stance=include_stance)
        if isinstance(out, dict):
            out = dict(out)
            out["deprecated"] = True
            out["canonical"] = "/api/paper/quant-bridge"
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
