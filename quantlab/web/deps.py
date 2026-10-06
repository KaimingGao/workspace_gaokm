"""Web 层共享服务实例（路由拆分后共用）。"""


import logging

logger = logging.getLogger(__name__)
# 启动时绑定行情适配器（ports 不硬 import skills）
try:
    from adapters.bind import bind_market_adapters

    bind_market_adapters()
except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
    logger.debug("catch except Exception: in deps.py", exc_info=True)
    pass

from quant.services.quant_service import QuantService
from services.chat_service import ChatService
from services.daily_service import DailyRunService
from services.eval_service import EvalService
from services.paper_service import PaperService
from services.platform_service import PlatformService
from services.watching_service import WatchingService

chat = ChatService()
paper = PaperService()
evals = EvalService()
daily = DailyRunService(paper=paper, evals=evals)
quant = QuantService()
platform = PlatformService()
watching = WatchingService()
