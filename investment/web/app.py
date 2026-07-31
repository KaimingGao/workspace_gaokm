"""FastAPI Web：量化交易工作台（对话 + 观察/模拟/回溯 + 校验）。"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.env import load_env_file  # noqa: E402
from core.paths import ROOT_DIR  # noqa: E402

load_env_file(os.path.join(ROOT_DIR, ".env"))
os.environ.setdefault("TQDM_DISABLE", "1")

try:
    from skills.common.ak_lock import install_akshare_lock

    install_akshare_lock()
except Exception:
    pass

from web import deps  # noqa: E402
from web.page_html import render_tool_html  # noqa: E402
from web.routers import chat, daily, evals, live_ws, meta, paper, platform, quant, strategy, watching  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 兼容查阅：与 web.deps 为同一实例（路由以 deps 为准）
_chat = deps.chat
_paper = deps.paper
_evals = deps.evals
_daily = deps.daily
_quant = deps.quant

app = FastAPI(
    title="Investment · 量化交易",
    description="量化交易 Web（融合 AI）：业务模块 + 全局 AI 命令",
    version="0.1.0",
)

app.include_router(meta.router)
app.include_router(chat.router)
app.include_router(paper.router)
app.include_router(strategy.router)
app.include_router(daily.router)
app.include_router(quant.router)
app.include_router(watching.router)
app.include_router(evals.router)
app.include_router(platform.router)
app.include_router(live_ws.router)


@app.get("/")
def index():
    """根路径进入数据中心（仪表盘已下线）。"""
    return RedirectResponse(url="/watching", status_code=302)


@app.get("/chat")
def chat_page(tab: str | None = None):
    """全屏对话已下线：统一走顶栏 AI 抽屉（⌘K）；旧书签重定向。"""
    if tab == "platform":
        return RedirectResponse(url="/platform", status_code=302)
    return RedirectResponse(url="/watching", status_code=302)


@app.get("/platform")
def platform_page():
    """系统设置（平台面板）：不复用全屏对话壳层。"""
    return HTMLResponse(
        render_tool_html("platform"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/quant")
def quant_page():
    return HTMLResponse(
        render_tool_html("quant"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/watching")
def watching_page():
    return HTMLResponse(
        render_tool_html("watching"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/strategy")
def strategy_page():
    return HTMLResponse(
        render_tool_html("strategy"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/replay")
def replay_page():
    return HTMLResponse(
        render_tool_html("replay"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/follow")
def follow_page():
    return HTMLResponse(
        render_tool_html("follow"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/paper")
def paper_page():
    """旧「纸面」入口并入模拟页；API 仍为 /api/paper（内部 canonical）。"""
    return RedirectResponse(url="/follow", status_code=302)


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import Response

    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
