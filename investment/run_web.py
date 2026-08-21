#!/usr/bin/env python3
"""启动 Investment Web 端。"""


import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.env import load_env_file
from core.paths import ROOT_DIR

load_env_file(os.path.join(ROOT_DIR, ".env"))

from core.market import register_symbol_resolver
try:
    from skills.common.quote_api import StockAPI
    register_symbol_resolver(StockAPI.resolve_symbol)
except Exception:
    import logging
    logging.getLogger(__name__).debug("StockAPI resolver not registered (skills layer unavailable at import time)")

# Web 进程不在终端刷 tqdm 进度条（进度改由页面展示）
os.environ.setdefault("TQDM_DISABLE", "1")


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def main():
    try:
        import uvicorn
    except ImportError:
        print("请先安装 Web 依赖：python3 -m pip install -r requirements.txt")
        sys.exit(1)

    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "8000"))
    # 默认关 reload：热重载会杀后台纸面任务；开发改静态/py 时设 WEB_RELOAD=1
    reload = _env_flag("WEB_RELOAD", "0")
    print("=" * 60)
    print("  Investment Web · 量化交易")
    print(f"  打开 http://{host}:{port}")
    print("  环境变量 WEB_HOST / WEB_PORT 可改监听地址")
    print(f"  WEB_RELOAD={'on' if reload else 'off'}（改代码热重载；会中断纸面后台任务）")
    if reload:
        print("  提示：确认调仓时请避免保存会触发 reload 的文件")
    print("=" * 60)
    uvicorn.run("web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
