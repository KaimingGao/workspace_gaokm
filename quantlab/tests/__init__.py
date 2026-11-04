"""测试包导入时隔离新闻库，避免 unittest 打进 data/store/news.db。"""

import os
import tempfile

os.environ.setdefault("QUANTLAB_NEWS_ARCHIVE_POLL", "0")
os.environ.setdefault("QUANTLAB_NEWS_ARCHIVE_INTERVAL", "0")
os.environ.setdefault(
    "QUANTLAB_NEWS_ARCHIVE_DIR",
    tempfile.mkdtemp(prefix="ql-news-archive-"),
)
