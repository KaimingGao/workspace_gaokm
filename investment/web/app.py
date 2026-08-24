"""FastAPI Web：量化交易工作台（对话 + 观察/模拟/回溯 + 校验）。"""


import logging

logger = logging.getLogger(__name__)
import os
import sys

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
    logger.debug("catch except Exception: in app.py", exc_info=True)
    pass

from web import deps  # noqa: E402
from web.page_html import render_tool_html  # noqa: E402
from web.routers import (  # noqa: E402
    chat,
    daily,
    evals,
    live_ws,
    meta,
    paper,
    platform,
    quant,
    quant_backtest,
    quant_cluster,
    quant_config,
    quant_dashboard,
    quant_research,
    quant_score,
    strategy,
    watching,
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 兼容查阅：与 web.deps 为同一实例（路由以 deps 为准）
_chat = deps.chat
_paper = deps.paper
_evals = deps.evals
_daily = deps.daily
_quant = deps.quant

@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    try:
        from core.paths import PAPER_PATH
        from core.t0.auto_worker import _load_enabled_flag, t0_auto_worker
        from services.paper_service import PaperService

        if _load_enabled_flag():
            try:
                PaperService(PAPER_PATH).set_t0_worker(True)
            except FileNotFoundError:
                t0_auto_worker.restore()
    except Exception:  # noqa: BLE001
        logger.debug("t0 auto worker restore skipped", exc_info=True)
    yield
    try:
        from core.t0.auto_worker import t0_auto_worker

        t0_auto_worker.stop()
    except Exception:  # noqa: BLE001
        logger.debug("t0 auto worker shutdown skipped", exc_info=True)


app = FastAPI(
    title="QuantLab · 量化交易",
    description="QuantLab 量化交易 Web（融合 AI）：业务模块 + 全局 AI 命令",
    version="0.1.0",
    lifespan=_app_lifespan,
)

app.include_router(meta.router)
app.include_router(chat.router)
app.include_router(paper.router)
app.include_router(strategy.router)
app.include_router(daily.router)
app.include_router(quant.router)
app.include_router(quant_config.router)
app.include_router(quant_research.router)
app.include_router(quant_cluster.router)
app.include_router(quant_backtest.router)
app.include_router(quant_score.router)
app.include_router(quant_dashboard.router)
app.include_router(watching.router)
app.include_router(evals.router)
app.include_router(platform.router)
app.include_router(live_ws.router)


@app.get("/")
def index() -> RedirectResponse:
    """根路径进入仪表盘。"""
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/chat")
def chat_page(tab: str | None = None) -> RedirectResponse:
    """全屏对话已下线：统一走顶栏 AI 抽屉（⌘K）；旧书签重定向。"""
    if tab == "platform":
        return RedirectResponse(url="/platform", status_code=302)
    return RedirectResponse(url="/watching", status_code=302)


@app.get("/platform")
def platform_page() -> HTMLResponse:
    """系统设置（平台面板）：不复用全屏对话壳层。"""
    return HTMLResponse(
        render_tool_html("platform"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/quant")
def quant_page() -> HTMLResponse:
    return HTMLResponse(
        render_tool_html("quant"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/watching")
def watching_page() -> HTMLResponse:
    return HTMLResponse(
        render_tool_html("watching"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/dashboard")
def dashboard_page() -> HTMLResponse:
    """仪表盘：全局 KPI · 净值曲线 · 板块热力 · 信号告警。"""
    return HTMLResponse(
        render_tool_html("dashboard"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/strategy")
def strategy_page() -> HTMLResponse:
    return HTMLResponse(
        render_tool_html("strategy"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/replay")
def replay_page() -> HTMLResponse:
    return HTMLResponse(
        render_tool_html("replay"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/follow")
def follow_page() -> HTMLResponse:
    return HTMLResponse(
        render_tool_html("follow"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/paper")
def paper_page() -> RedirectResponse:
    """旧「纸面」入口并入模拟页；API 仍为 /api/paper（内部 canonical）。"""
    return RedirectResponse(url="/follow", status_code=302)


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.middleware("http")
async def _static_no_store(request, call_next):
    """嵌套 ES module 无 ?v= 时避免浏览器 304 旧脚本。"""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
