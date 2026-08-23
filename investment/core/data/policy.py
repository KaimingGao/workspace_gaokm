"""DataService / store TTL 与质量阈值（DS-R1）。

上层与 skills 应引用本模块常量，避免魔法数字散落。
"""


import logging

logger = logging.getLogger(__name__)
# —— 缓存 TTL ——
DAILY_CACHE_HOURS = 24.0
FUNDAMENTALS_CACHE_HOURS = 24.0
NEWS_CACHE_HOURS = 6.0
MINUTE_CACHE_HOURS = 12.0
QUOTE_MEM_TTL_SECONDS = 60
QUOTE_STALE_MAX_SECONDS = 1800
SPOT_DISK_MAX_AGE_HOURS = 24.0 * 14
COVERAGE_STALE_HOURS = 36.0
VALUATION_CACHE_HOURS = 36.0

# —— 质量 / 增量 ——
QUALITY_STALE_BAR_DAYS = 10
THIN_MIN_BARS = 15
QFQ_LONG_GAP_DAYS = 40
DAILY_BARS_MAX_KEEP = 800
MINUTE_BARS_MAX_KEEP = 5000
FUNDAMENTALS_HISTORY_MAX_POINTS = 40

# —— 财务 PIT 门禁（DS-R3）——
ANN_MISSING_CODE_RATIO_WARN = 0.3
ANN_MISSING_CODE_RATIO_BLOCK = 0.5
DEFAULT_ANN_MISSING_POLICY = "zero_weight"  # warn | zero_weight | hard_reject
DEFAULT_MISSING_AS_OF_POLICY = "zero_weight"
DEFAULT_ADJUST_POLICY = "qfq"

# —— 行业 ——
UNMAPPED_SECTOR = "未分类"
MIN_SECTOR_MAP_COVERAGE_WARN = 0.5
MIN_SECTOR_MAP_COVERAGE_BLOCK = 0.3
DEFAULT_REQUIRE_SECTOR_MAP = False
# 板别启发式标签（不得当作行业限额键；历史 sync 可能已写入 sector_map）
BOARD_LABELS = frozenset(
    {
        "科创",
        "创业板",
        "主板沪",
        "主板深",
        "港股",
        "其他",
        "未分类",
        "美股",
    }
)


def is_board_label(label: str | None) -> bool:
    s = str(label or "").strip()
    if not s:
        return True
    if s in BOARD_LABELS:
        return True
    if s.startswith("板别:") or s.startswith("板别："):
        return True
    return False


# 兼容旧名
FUNDAMENTALS_CACHE_HOURS_ALIAS = FUNDAMENTALS_CACHE_HOURS
NEWS_CACHE_HOURS_ALIAS = NEWS_CACHE_HOURS
