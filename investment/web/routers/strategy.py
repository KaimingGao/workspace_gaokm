"""策略规格 / 晋级 API（Q2）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import StrategyPromoteRequest

router = APIRouter(tags=["strategy"])


@router.get("/api/strategy")
def strategy_list():
    return deps.paper.list_strategies()


@router.post("/api/strategy/promote")
def strategy_promote(body: StrategyPromoteRequest):
    try:
        return deps.paper.promote_strategy(
            body.strategy,
            note=body.note or "",
            apply_to_paper=body.apply_to_paper,
            overrides=body.overrides,
        )
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
