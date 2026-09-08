"""静态资源缓存版本号：HTML 与 JS 模块共用，只改这一处。"""


import logging

logger = logging.getLogger(__name__)
# 改 UI / CSS / 任一 web/static/js 后 bump；模板与 app.js 经 {{ASSET_V}} / window.__ASSET_V__ 注入
ASSET_V = "p2045"
