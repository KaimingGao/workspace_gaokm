"""Watching / 观察名单 API。路由只做 HTTP；业务进 WatchingService / QuantService。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import WatchingFile, WatchingSyncPaper, WatchingWatchAdd, WatchingWatchRemove

router = APIRouter(tags=["watching"])

# 与 insights/sentiment 隔离，避免默认线程池被占满后行情 18s 排队 504
_quotes_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="watch-quotes")
_QUOTES_WAIT_SEC = 12.0


def _quotes_placeholder(codes: Optional[list], *, note: str) -> dict:
    items = []
    for c in codes or []:
        items.append(
            {
                "stock_code": c,
                "stock_name": c,
                "ok": False,
                "error": note,
                "price": None,
                "price_raw": None,
                "change_percent": None,
                "change_amount": None,
                "open": None,
                "high": None,
                "low": None,
                "volume": None,
                "market": None,
            }
        )
    return {"ok": True, "count": len(items), "items": items, "note": note}


def _parse_codes(codes: str = "") -> Optional[list]:
    raw = (codes or "").strip()
    if not raw:
        return None
    return [c.strip() for c in raw.replace(";", ",").split(",") if c.strip()]


@router.get("/api/watching/file")
def watching_file():
    try:
        return deps.quant.read_watching_file()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/watching")
def watching_get():
    return deps.quant.read_watching()


@router.get("/api/watching/search")
def watching_search(q: str = "", limit: int = 8):
    try:
        return deps.watching.search(q, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/watching/quotes")
async def watching_quotes(codes: str = ""):
    import asyncio

    parsed = _parse_codes(codes)
    loop = asyncio.get_running_loop()
    try:
        # 独立线程池 + 短超时；超时返回占位而非 504，避免前端整表失败
        return await asyncio.wait_for(
            loop.run_in_executor(_quotes_executor, deps.watching.quotes, parsed),
            timeout=_QUOTES_WAIT_SEC,
        )
    except asyncio.TimeoutError:
        return _quotes_placeholder(parsed, note="行情拉取超时，请稍后刷新")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/watching/insights")
async def watching_insights(codes: str = ""):
    """观察研究摘要（评分/倾向/超额等）。放到线程池，避免堵住 Web 事件循环。"""
    import asyncio

    try:
        parsed = _parse_codes(codes)
        return await asyncio.to_thread(deps.watching.insights, parsed)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/watching/sentiment/alerts")
async def watching_sentiment_alerts():
    import asyncio

    try:
        return await asyncio.to_thread(deps.watching.sentiment_alerts)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/watching/sentiment")
async def watching_sentiment(codes: str = "", limit: int = 3, force: bool = False):
    import asyncio

    try:
        parsed = _parse_codes(codes)

        def _run():
            return deps.watching.sentiment_list(parsed, limit=limit, force=force)

        # 批量串行拉取；单票约 12s 上限，整批给足余量但避免永久挂起
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=90)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="舆情批量拉取超时，请稍后重试") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/watching/sentiment/{code}")
async def watching_sentiment_one(code: str, limit: int = 8, force: bool = False):
    import asyncio

    try:
        def _run():
            return deps.watching.sentiment_one(code, limit=limit, force=force)

        # 须低于前端 AbortController(20s)，否则 UI 先 abort 只显示「加载超时」
        return await asyncio.wait_for(asyncio.to_thread(_run), timeout=18)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="资讯拉取超时，请稍后重试") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/watching/sentiment/{code}/analysis")
async def watching_sentiment_analysis(code: str):
    import asyncio

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(deps.watching.sentiment_analysis, code),
            timeout=45,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "analysis": "AI 分析超时，请稍后重试"}
    except Exception as e:
        return {"ok": False, "analysis": f"分析失败: {str(e)}"}


@router.post("/api/watching/watchlist/add")
def watching_watchlist_add(body: WatchingWatchAdd):
    try:
        return deps.watching.add_watch(body.query, sync_paper=body.sync_paper)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/watching/watchlist/remove")
def watching_watchlist_remove(body: WatchingWatchRemove):
    try:
        return deps.watching.remove_watch(body.code, sync_paper=body.sync_paper)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/watching/health")
def watching_health():
    return deps.quant.check_watching_health()


@router.post("/api/watching/init")
def watching_init():
    try:
        path = deps.watching.init()
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True, "path": path}


@router.post("/api/watching/refresh")
def watching_refresh(sync_paper: bool = False):
    try:
        return deps.quant.refresh_watching(sync_paper=sync_paper)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/watching/sync-paper/preview")
def watching_sync_paper_preview(body: Optional[WatchingSyncPaper] = None):
    try:
        b = body
        return deps.quant.plan_watching_to_paper(
            codes=b.codes if b is not None else None,
            shares=b.shares if b is not None else None,
            shares_by_code=b.shares_by_code if b is not None else None,
            amount_per_code=b.amount_per_code if b is not None else None,
            amount_by_code=b.amount_by_code if b is not None else None,
            position_pct=b.position_pct if b is not None else None,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/watching/sync-paper")
def watching_sync_paper(body: Optional[WatchingSyncPaper] = None):
    try:
        b = body
        return deps.quant.sync_watching_to_paper(
            codes=b.codes if b is not None else None,
            shares=b.shares if b is not None else None,
            shares_by_code=b.shares_by_code if b is not None else None,
            amount_per_code=b.amount_per_code if b is not None else None,
            amount_by_code=b.amount_by_code if b is not None else None,
            position_pct=b.position_pct if b is not None else None,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/api/watching/file")
def watching_save(body: WatchingFile):
    try:
        path = deps.watching.save_file(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "path": path}


@router.get("/api/watching/daily-chart")
def watching_daily_chart(code: str, lookback: int = 60):
    """通用日线数据，供观察页展示日线图。"""
    from core.data_service import get_bars, get_quote

    c = str(code or "").strip()
    if not c:
        raise HTTPException(status_code=400, detail="请指定股票代码")

    fetched = get_bars(c, limit=max(10, min(int(lookback), 120)))
    bars = fetched.get("bars") or []
    src = fetched.get("data_source")
    points = []
    for b in bars or []:
        close = b.get("close")
        try:
            px = float(close)
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        points.append(
            {
                "date": str(b.get("date") or ""),
                "close": round(px, 4),
                "high": float(b.get("high") or 0) or None,
                "low": float(b.get("low") or 0) or None,
                "volume": float(b.get("volume") or 0) or None,
            }
        )

    name = c
    try:
        quote = get_quote(c)
        if quote.get("success"):
            name = str(quote.get("stock_name") or "").strip() or c
    except Exception:
        pass

    return {
        "ok": True,
        "stock_code": c,
        "stock_name": name,
        "data_source": src,
        "points": points,
        "point_count": len(points),
    }
