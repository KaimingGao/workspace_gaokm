"""Daily preset / health API。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from quant.ops.daily_presets import list_daily_presets
from web import deps
from web.schemas import DailyRunRequest

router = APIRouter(tags=["daily"])


@router.get("/api/daily/presets")
def daily_presets():
    return {"success": True, "presets": list_daily_presets()}


@router.get("/api/daily/last")
def daily_last():
    return deps.daily.load_last_run()


@router.get("/api/daily/health")
def daily_health():
    return deps.quant.build_health_summary()


@router.post("/api/daily/run")
def daily_run(body: DailyRunRequest):
    try:
        result = deps.daily.run(
            preset=body.preset,
            paper_run=body.paper_run,
            paper_buy=body.paper_buy,
            eval_mock=body.eval_mock,
            eval_agent=body.eval_agent,
            quant_report=body.quant_report,
            watching_refresh=body.watching_refresh,
            cross_section=body.cross_section,
            sync_paper_watchlist=body.sync_paper_watchlist,
            paper_rebalance=body.paper_rebalance,
            export_quant_report=body.export_quant_report,
            portfolio_neutral_compare=body.portfolio_neutral_compare,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not result.get("ok"):
        return JSONResponse(status_code=422, content=result)
    return result
