"""DataService / store TTL 与质量阈值（DS-R1）。

上层与 skills 应引用本模块常量，避免魔法数字散落。
"""


import os
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
# 观察池日 K 写入窗 1000 个交易日，再垫 Alpha158（约 62 根）。须 ≥ 拉取条数，否则增量补齐会把齐窗当成短仓反复整窗重拉。
DAILY_BARS_MAX_KEEP = 1200
# 5m 全日约 48 根；12000 ≈ 250 交易日，供 BaoStock 回填 + path 150d 回看
MINUTE_BARS_MAX_KEEP = 12000
# 远端分钟拉取后休眠（东财 / 新浪腾讯 / BaoStock 各睡一次）；降低反爬封 IP 风险
MINUTE_FETCH_DELAY_SEC = 20.0
MINUTE_FETCH_DELAY_MAX_SEC = 30.0
# BaoStock 单票 query+遍历无内置超时；子进程 join 超时后 kill，避免强更整批挂死
MINUTE_BAOSTOCK_TIMEOUT_SEC = 90.0
MINUTE_BAOSTOCK_TIMEOUT_MAX_SEC = 180.0
# 观察池分钟强更/增量：整票子进程隔离超时（东财全窗 / skip_em 近端）
MINUTE_ISOLATED_TIMEOUT_SEC = 90.0
MINUTE_ISOLATED_TIMEOUT_SKIP_EM_SEC = 45.0
MINUTE_ISOLATED_TIMEOUT_MAX_SEC = 180.0
# 东财 / BaoStock 分钟窗口（日历日）；强更 lookback 与此对齐
MINUTE_EM_LOOKBACK_DAYS = 120
MINUTE_EM_LOOKBACK_MAX_DAYS = 120
# BaoStock 分钟窗口（日历日）
MINUTE_BAOSTOCK_LOOKBACK_DAYS = 30
MINUTE_BAOSTOCK_LOOKBACK_MAX_DAYS = 90
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
    """东财 / 新浪腾讯 / BaoStock 分钟远端拉取后间隔（秒）；``QUANTLAB_MINUTE_FETCH_DELAY_SEC`` 可覆盖。"""
    raw = os.environ.get("QUANTLAB_MINUTE_FETCH_DELAY_SEC", str(MINUTE_FETCH_DELAY_SEC))
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(MINUTE_FETCH_DELAY_SEC)
    return max(0.0, min(v, float(MINUTE_FETCH_DELAY_MAX_SEC)))


def minute_warmup_skip_em() -> bool:
    """批量预热默认走东财；``QUANTLAB_MINUTE_WARMUP_SKIP_EM=1`` 跳过东财（stock_zh_a_minute→BaoStock）。"""
    raw = os.environ.get("QUANTLAB_MINUTE_WARMUP_SKIP_EM", "0").strip().lower()
    return raw not in ("0", "false", "no", "off")


MINUTE_WARMUP_STALE_HOURS = 24.0
MINUTE_WARMUP_READY_MIN_SPAN_DAYS = 40
MINUTE_WARMUP_MAX_CAL_GAP_DAYS = 4


def minute_warmup_skip_if_ready() -> bool:
    """批量分钟预热：本地已 Ready 则跳过远端（``QUANTLAB_MINUTE_WARMUP_SKIP_IF_READY=0`` 关闭）。"""
    raw = os.environ.get("QUANTLAB_MINUTE_WARMUP_SKIP_IF_READY", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def minute_warmup_ready_min_span_days() -> int:
    """与 UI Ready 闸一致：最近这么多个交易日必须无缺。默认 40。"""
    raw = os.environ.get(
        "QUANTLAB_MINUTE_WARMUP_READY_MIN_SPAN_DAYS", str(MINUTE_WARMUP_READY_MIN_SPAN_DAYS)
    )
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = int(MINUTE_WARMUP_READY_MIN_SPAN_DAYS)
    return max(1, min(v, 120))


def minute_warmup_stale_hours() -> float:
    raw = os.environ.get(
        "QUANTLAB_MINUTE_WARMUP_STALE_HOURS", str(MINUTE_WARMUP_STALE_HOURS)
    )
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(MINUTE_WARMUP_STALE_HOURS)
    return max(1.0, min(v, 168.0))


def minute_baostock_timeout_sec() -> float:
    """BaoStock 分钟拉取超时（秒）；``QUANTLAB_MINUTE_BS_TIMEOUT_SEC=0`` 关闭子进程隔离。"""
    raw = os.environ.get(
        "QUANTLAB_MINUTE_BS_TIMEOUT_SEC", str(MINUTE_BAOSTOCK_TIMEOUT_SEC)
    )
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(MINUTE_BAOSTOCK_TIMEOUT_SEC)
    if v <= 0:
        return 0.0
    return max(1.0, min(v, float(MINUTE_BAOSTOCK_TIMEOUT_MAX_SEC)))


def minute_isolated_timeout_sec(*, skip_em: bool = False) -> float:
    """观察池分钟强更/增量单票子进程超时（秒）。

    ``QUANTLAB_MINUTE_ISOLATED_TIMEOUT_SEC`` 覆盖东财全窗默认；
    ``QUANTLAB_MINUTE_ISOLATED_TIMEOUT_SKIP_EM_SEC`` 覆盖近端（skip_em）默认；
    ``0`` 关闭隔离（退回进程内直调，仅单测）。
    """
    if skip_em:
        raw = os.environ.get(
            "QUANTLAB_MINUTE_ISOLATED_TIMEOUT_SKIP_EM_SEC",
            str(MINUTE_ISOLATED_TIMEOUT_SKIP_EM_SEC),
        )
        default = float(MINUTE_ISOLATED_TIMEOUT_SKIP_EM_SEC)
    else:
        raw = os.environ.get(
            "QUANTLAB_MINUTE_ISOLATED_TIMEOUT_SEC",
            str(MINUTE_ISOLATED_TIMEOUT_SEC),
        )
        default = float(MINUTE_ISOLATED_TIMEOUT_SEC)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = default
    if v <= 0:
        return 0.0
    return max(1.0, min(v, float(MINUTE_ISOLATED_TIMEOUT_MAX_SEC)))


def minute_baostock_lookback_days() -> int:
    """BaoStock 分钟回看日历日；``QUANTLAB_MINUTE_BS_LOOKBACK_DAYS`` 可覆盖。"""
    raw = os.environ.get(
        "QUANTLAB_MINUTE_BS_LOOKBACK_DAYS", str(MINUTE_BAOSTOCK_LOOKBACK_DAYS)
    )
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = int(MINUTE_BAOSTOCK_LOOKBACK_DAYS)
    return max(5, min(v, int(MINUTE_BAOSTOCK_LOOKBACK_MAX_DAYS)))


def minute_em_lookback_days() -> int:
    """东财分钟回看日历日；``QUANTLAB_MINUTE_EM_LOOKBACK_DAYS`` 可覆盖。"""
    raw = os.environ.get(
        "QUANTLAB_MINUTE_EM_LOOKBACK_DAYS", str(MINUTE_EM_LOOKBACK_DAYS)
    )
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = int(MINUTE_EM_LOOKBACK_DAYS)
    return max(5, min(v, int(MINUTE_EM_LOOKBACK_MAX_DAYS)))


# 兼容旧名
FUNDAMENTALS_CACHE_HOURS_ALIAS = FUNDAMENTALS_CACHE_HOURS
NEWS_CACHE_HOURS_ALIAS = NEWS_CACHE_HOURS
