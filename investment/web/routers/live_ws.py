"""WebSocket 实况推送（W4）：纸面摘要 / 健康 / 告警。

节流推送；断线由前端重连。不代客下单、不推 Tick 盘口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["live"])

# 客户端集合；单进程研究台足够
_clients: Set[WebSocket] = set()
_pump_task: Optional[asyncio.Task] = None
_INTERVAL_SEC = 3.0


def _snapshot() -> Dict[str, Any]:
    """组装一次实况快照（失败字段降级为空）。"""
    out: Dict[str, Any] = {
        "type": "snapshot",
        "ts": time.time(),
        "paper": None,
        "health": None,
        "alerts": None,
    }
    try:
        from web import deps

        paper = deps.paper.status()
        if isinstance(paper, dict):
            sm = paper.get("summary") or paper
            snaps = paper.get("snapshots") or []
            out["paper"] = {
                "initialized": bool(paper.get("initialized")),
                "equity": sm.get("equity") or sm.get("total_equity"),
                "cash": sm.get("cash"),
                "position_count": sm.get("position_count")
                or (len(sm.get("holdings") or []) if isinstance(sm.get("holdings"), list) else None),
                "total_pnl_pct": sm.get("total_pnl_pct"),
                "invested_pct": sm.get("invested_pct"),
                "max_drawdown_pct": sm.get("max_drawdown_pct"),
                "strategy_id": paper.get("strategy_id") or (sm.get("strategy_id")),
                "snapshot_tail": snaps[-3:] if isinstance(snaps, list) else [],
            }
    except Exception as e:
        out["paper_error"] = str(e)

    try:
        from quant.ops.daily_health import build_daily_health

        h = build_daily_health()
        out["health"] = {
            "ok": bool(h.get("ok") or h.get("success")),
            "issues": (h.get("issues") or [])[:8],
            "warnings": (h.get("warnings") or [])[:5],
        }
    except Exception as e:
        out["health_error"] = str(e)

    try:
        from web.audit_timeline import read_alerts_last

        out["alerts"] = read_alerts_last()
    except Exception as e:
        out["alerts_error"] = str(e)

    return out


async def _broadcast(payload: Dict[str, Any]) -> None:
    if not _clients:
        return
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    dead: list[WebSocket] = []
    for ws in list(_clients):
        try:
            await ws.send_text(raw)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


async def _pump_loop() -> None:
    while True:
        try:
            if _clients:
                snap = await asyncio.to_thread(_snapshot)
                await _broadcast(snap)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ws live pump failed")
        await asyncio.sleep(_INTERVAL_SEC)


def _ensure_pump() -> None:
    global _pump_task
    if _pump_task is None or _pump_task.done():
        _pump_task = asyncio.create_task(_pump_loop())


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    _ensure_pump()
    try:
        # 立即推一帧
        snap = await asyncio.to_thread(_snapshot)
        await websocket.send_text(json.dumps(snap, ensure_ascii=False, default=str))
        while True:
            # 客户端可发 ping / refresh
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg) if msg else {}
            except json.JSONDecodeError:
                data = {}
            if isinstance(data, dict) and data.get("type") in ("ping", "refresh", "hello"):
                snap = await asyncio.to_thread(_snapshot)
                snap["type"] = "snapshot"
                await websocket.send_text(json.dumps(snap, ensure_ascii=False, default=str))
            else:
                await websocket.send_text(json.dumps({"type": "pong", "ts": time.time()}))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("ws client closed", exc_info=True)
    finally:
        _clients.discard(websocket)
