"""DataService / store TTL 与质量阈值（DS-R1）。

上层与 skills 应引用本模块常量，避免魔法数字散落。
"""


import logging
import os

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
# 5m 全日约 48 根；12000 ≈ 250 交易日，供 BaoStock 回填 + path 150d 回看
MINUTE_BARS_MAX_KEEP = 12000
# 批量预热/强更：每只票 AkShare（东财）远端拉取后休眠，降低反爬封 IP 风险
MINUTE_FETCH_DELAY_SEC = 2.0
MINUTE_FETCH_DELAY_MAX_SEC = 5.0
# BaoStock 单票 query+遍历无内置超时；子进程 join 超时后 kill，避免强更整批挂死
MINUTE_BAOSTOCK_TIMEOUT_SEC = 90.0
MINUTE_BAOSTOCK_TIMEOUT_MAX_SEC = 180.0
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


def minute_fetch_delay_sec() -> float:
    """AkShare 分钟批量拉取间隔（秒）；``INVESTMENT_MINUTE_FETCH_DELAY_SEC`` 可覆盖。"""
    raw = os.environ.get("INVESTMENT_MINUTE_FETCH_DELAY_SEC", str(MINUTE_FETCH_DELAY_SEC))
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(MINUTE_FETCH_DELAY_SEC)
    return max(0.0, min(v, float(MINUTE_FETCH_DELAY_MAX_SEC)))


def minute_warmup_skip_em() -> bool:
    """批量预热默认跳过东财，仅 BaoStock + 本地 merge（``INVESTMENT_MINUTE_WARMUP_SKIP_EM=0`` 恢复东财）。"""
    raw = os.environ.get("INVESTMENT_MINUTE_WARMUP_SKIP_EM", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


MINUTE_WARMUP_STALE_HOURS = 24.0
MINUTE_WARMUP_READY_MIN_SPAN_DAYS = 40
MINUTE_WARMUP_MAX_CAL_GAP_DAYS = 4


def minute_warmup_skip_if_ready() -> bool:
    """批量分钟预热：本地已 Ready 则跳过远端（``INVESTMENT_MINUTE_WARMUP_SKIP_IF_READY=0`` 关闭）。"""
    raw = os.environ.get("INVESTMENT_MINUTE_WARMUP_SKIP_IF_READY", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def minute_warmup_ready_min_span_days() -> int:
    """与 UI Ready 闸一致，默认 40 交易日（有 bar 的日数）。"""
    raw = os.environ.get(
        "INVESTMENT_MINUTE_WARMUP_READY_MIN_SPAN_DAYS", str(MINUTE_WARMUP_READY_MIN_SPAN_DAYS)
    )
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = int(MINUTE_WARMUP_READY_MIN_SPAN_DAYS)
    return max(1, min(v, 120))


def minute_warmup_stale_hours() -> float:
    raw = os.environ.get(
        "INVESTMENT_MINUTE_WARMUP_STALE_HOURS", str(MINUTE_WARMUP_STALE_HOURS)
    )
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(MINUTE_WARMUP_STALE_HOURS)
    return max(1.0, min(v, 168.0))


def minute_baostock_timeout_sec() -> float:
    """BaoStock 分钟拉取超时（秒）；``INVESTMENT_MINUTE_BS_TIMEOUT_SEC=0`` 关闭子进程隔离。"""
    raw = os.environ.get(
        "INVESTMENT_MINUTE_BS_TIMEOUT_SEC", str(MINUTE_BAOSTOCK_TIMEOUT_SEC)
    )
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(MINUTE_BAOSTOCK_TIMEOUT_SEC)
    if v <= 0:
        return 0.0
    return max(1.0, min(v, float(MINUTE_BAOSTOCK_TIMEOUT_MAX_SEC)))


# 兼容旧名
FUNDAMENTALS_CACHE_HOURS_ALIAS = FUNDAMENTALS_CACHE_HOURS
NEWS_CACHE_HOURS_ALIAS = NEWS_CACHE_HOURS
