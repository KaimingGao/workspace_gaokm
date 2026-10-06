"""Daily preset / health API。"""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from quant.ops.daily_presets import list_daily_presets
from quant.research.portfolio_data import daily_bt_option_defaults
from web import deps
from web.schemas import DailyRunRequest

router = APIRouter(tags=["daily"])

@router.get("/api/daily/presets")

def daily_presets() -> Dict[str, Any]:
    return {
        "success": True,
        "presets": list_daily_presets(),
        "bt_defaults": daily_bt_option_defaults(),
    }

@router.get("/api/daily/last")

def daily_last() -> Dict[str, Any]:
    return deps.daily.load_last_run()

@router.get("/api/daily/health")

def daily_health() -> Dict[str, Any]:
    return deps.quant.build_health_summary()

@router.post("/api/daily/run")

def daily_run(body: DailyRunRequest) -> Any:
    try:
        result = deps.daily.run(
            preset=body.preset,
            paper_run=body.paper_run,
            paper_holding_cycle=body.paper_holding_cycle,
            paper_buy=body.paper_buy,
            eval_mock=body.eval_mock,
            eval_agent=body.eval_agent,
            quant_report=body.quant_report,
            watching_refresh=body.watching_refresh,
            cross_section=body.cross_section,
            sync_paper_watchlist=body.sync_paper_watchlist,
            paper_rebalance=body.paper_rebalance,
            paper_cross_section_rebalance=body.paper_cross_section_rebalance,
            export_quant_report=body.export_quant_report,
            lookback=body.lookback,
            fusion_w_co=body.fusion_w_co,
            rank_enter=body.rank_enter,
            rank_strong=body.rank_strong,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    if not result.get("ok"):
        return JSONResponse(status_code=422, content=result)
    return result
