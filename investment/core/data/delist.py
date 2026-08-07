"""退市股票处理模块：管理退市日历，消除回测幸存者偏差。

幸存者偏差：回测时若只包含当前仍上市的股票，忽略已退市股票的历史数据，
会导致回测收益虚高。本模块维护一份退市日历（JSON），并提供：

- 退市日历的加载/保存（原子写入）
- 单只股票的退市状态判断
- 幸存者偏差过滤（保留退市股票退市日前的历史，或完全剔除）
- 股票池幸存者偏差风险评估
- 从 AkShare 拉取 A 股退市列表并更新日历

退市日历文件路径：``DATA_DIR/delist_calendar.json``
格式：``{"000001": {"delist_date": "2020-08-20", "name": "平安银行", "reason": "主动退市"}, ...}``
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from core.paths import DATA_DIR

logger = logging.getLogger(__name__)

DELIST_CALENDAR_PATH = os.path.join(DATA_DIR, "delist_calendar.json")
_UPDATE_TTL_DAYS = 30


def _atomic_write_json(path: str, data: Any) -> None:
    """内联原子写盘（``core.io_atomic`` 不可用时的回退实现）。

    先写临时文件再 ``os.replace``，避免半文件被读取。
    """
    import tempfile

    dirname = os.path.dirname(path) or "."
    os.makedirs(dirname, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirname, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _bar_date(bar: dict) -> str:
    """取 bar 的日期字段（兼容 date/trade_date）。"""
    b = bar or {}
    return str(b.get("date") or b.get("trade_date") or "")


def _norm_date(s: str) -> str:
    """把多种日期串归一化为 ``YYYY-MM-DD``；无法解析返回空串。"""
    s = (s or "").strip().replace("/", "-")
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def load_delist_calendar(path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """加载退市日历。

    :param path: 日历文件路径，None 时使用默认 ``DELIST_CALENDAR_PATH``
    :return: ``{stock_code: {"delist_date", "name", "reason"}}``
    文件不存在或损坏时返回空字典。
    """
    p = path or DELIST_CALENDAR_PATH
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.warning("退市日历读取失败：%s", p, exc_info=True)
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for code, info in data.items():
        if not isinstance(info, dict):
            continue
        ddate = str(info.get("delist_date") or "")
        if not ddate:
            continue
        out[str(code)] = {
            "delist_date": ddate,
            "name": str(info.get("name") or ""),
            "reason": str(info.get("reason") or ""),
        }
    return out


def save_delist_calendar(calendar: Dict[str, Dict[str, str]], path: Optional[str] = None) -> None:
    """保存退市日历（原子写入：tmp + os.replace）。

    :param calendar: 退市日历字典
    :param path: 目标路径，None 时使用默认 ``DELIST_CALENDAR_PATH``
    """
    p = path or DELIST_CALENDAR_PATH
    try:
        from core.io_atomic import atomic_write_json

        atomic_write_json(p, calendar)
    except ImportError:
        _atomic_write_json(p, calendar)


def is_delisted(code: str, as_of: str, calendar: Optional[Dict] = None) -> bool:
    """判断股票在 ``as_of`` 日期是否已退市。

    :param code: 股票代码
    :param as_of: 截止日期 ``YYYY-MM-DD``；空串表示不按日期过滤
        （只要日历中有退市记录即视为已退市）
    :param calendar: 退市日历，None 时加载默认日历
    :return: ``delist_date <= as_of`` 时返回 True
    """
    cal = calendar if calendar is not None else load_delist_calendar()
    info = cal.get(str(code))
    if not isinstance(info, dict):
        return False
    ddate = str(info.get("delist_date") or "")
    if not ddate:
        return False
    if not as_of:
        return True
    return ddate <= as_of


def delist_date_of(code: str, calendar: Optional[Dict] = None) -> Optional[str]:
    """获取股票退市日期；未退市返回 None。

    :param code: 股票代码
    :param calendar: 退市日历，None 时加载默认日历
    :return: 退市日期字符串 ``YYYY-MM-DD``，或 None
    """
    cal = calendar if calendar is not None else load_delist_calendar()
    info = cal.get(str(code))
    if not isinstance(info, dict):
        return None
    ddate = str(info.get("delist_date") or "")
    return ddate or None


def filter_survivorship(
    stock_bars: Dict[str, List[dict]],
    *,
    as_of: str = "",
    calendar: Optional[Dict] = None,
    keep_delisted_history: bool = True,
) -> Tuple[Dict[str, List[dict]], List[Dict[str, Any]]]:
    """幸存者偏差过滤（核心函数）。

    :param stock_bars: ``{code: [bar, ...]}`` 股票日线数据
    :param as_of: 回测截止日期（空串表示不按日期过滤，凡有退市记录均视为已退市）
    :param calendar: 退市日历，None 时加载默认日历
    :param keep_delisted_history: True=保留退市股票截至退市日（含当天）的历史；
        False=完全剔除退市股票
    :return: ``(filtered_bars, adjustments)``，其中 adjustments 每条形如
        ``{code, action: "truncated"/"removed", delist_date, original_bars, kept_bars}``

    算法：
      a. 加载退市日历；
      b. 对每只股票：
         - 已退市且 keep_delisted_history=True：截断 bars 到退市日（含当天）；
         - 已退市且 keep_delisted_history=False：完全剔除；
         - 未退市：保留全部；
      c. 记录被处理的股票信息。
    """
    cal = calendar if calendar is not None else load_delist_calendar()
    filtered: Dict[str, List[dict]] = {}
    adjustments: List[Dict[str, Any]] = []

    for code, bars in (stock_bars or {}).items():
        ddate = delist_date_of(code, cal)
        bars_list: List[dict] = list(bars or [])
        original_n = len(bars_list)

        if not ddate or not (not as_of or ddate <= as_of):
            # 未退市（或退市日晚于 as_of）：保留全部
            filtered[code] = bars_list
            continue

        # 已退市
        if not keep_delisted_history:
            adjustments.append(
                {
                    "code": code,
                    "action": "removed",
                    "delist_date": ddate,
                    "original_bars": original_n,
                    "kept_bars": 0,
                }
            )
            continue

        # 截断：保留 date <= delist_date（含退市日当天，因为退市日通常有最后收盘价）
        dates = np.array([_bar_date(b) for b in bars_list], dtype=str)
        keep_mask = dates <= ddate
        kept = [b for b, m in zip(bars_list, keep_mask) if m]
        filtered[code] = kept
        adjustments.append(
            {
                "code": code,
                "action": "truncated",
                "delist_date": ddate,
                "original_bars": original_n,
                "kept_bars": len(kept),
            }
        )

    return filtered, adjustments


def validate_universe_survivorship(
    stock_bars: Dict[str, List[dict]],
    *,
    start_date: str,
    end_date: str,
    calendar: Optional[Dict] = None,
) -> Dict[str, Any]:
    """验证股票池的幸存者偏差状况。

    检查 ``[start_date, end_date]`` 区间内：
      - 多少股票全程存活；
      - 多少股票中途退市（退市日落在区间内）；
      - 多少股票在 start_date 前已退市（不应出现在池中）。

    :return: ``{"total", "survived_full", "delisted_during", "delisted_before",
        "survivorship_bias_risk": "high"/"medium"/"low", "details": [...]}``

    风险等级：(delisted_during + delisted_before) > 0 且占比 > 10% → high；
    > 0 → medium；= 0 → low。
    """
    cal = calendar if calendar is not None else load_delist_calendar()
    total = len(stock_bars or {})
    survived_full = 0
    delisted_during = 0
    delisted_before = 0
    details: List[Dict[str, Any]] = []

    for code in (stock_bars or {}):
        ddate = delist_date_of(code, cal)
        if not ddate:
            survived_full += 1
            details.append({"code": code, "status": "survived", "delist_date": None})
            continue
        if ddate < start_date:
            delisted_before += 1
            details.append({"code": code, "status": "delisted_before", "delist_date": ddate})
        elif ddate <= end_date:
            delisted_during += 1
            details.append({"code": code, "status": "delisted_during", "delist_date": ddate})
        else:
            # 退市日晚于回测区间，区间内仍全程存活
            survived_full += 1
            details.append({"code": code, "status": "survived", "delist_date": ddate})

    excluded = delisted_during + delisted_before
    ratio = (excluded / total) if total > 0 else 0.0
    if excluded > 0 and ratio > 0.10:
        risk = "high"
    elif excluded > 0:
        risk = "medium"
    else:
        risk = "low"

    return {
        "total": total,
        "survived_full": survived_full,
        "delisted_during": delisted_during,
        "delisted_before": delisted_before,
        "survivorship_bias_risk": risk,
        "details": details,
    }


def _pick_col(cols: List[str], candidates: List[str]) -> Optional[str]:
    """从 DataFrame 列名中按候选名精确/子串匹配选出一列。"""
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    for cand in candidates:
        for c in cols:
            if cand.lower() in c.lower():
                return c
    return None


def _normalize_delist_df(
    df: Any,
    *,
    code_cols: List[str],
    name_cols: List[str],
    date_cols: List[str],
) -> Dict[str, Dict[str, str]]:
    """把 AkShare 退市 DataFrame 归一化为退市日历字典。"""
    if df is None or getattr(df, "empty", True):
        return {}
    cols = list(df.columns)
    code_col = _pick_col(cols, code_cols)
    name_col = _pick_col(cols, name_cols)
    date_col = _pick_col(cols, date_cols)
    if not code_col or not date_col:
        return {}

    out: Dict[str, Dict[str, str]] = {}
    for _, row in df.iterrows():
        code = str(row.get(code_col) or "").strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        ddate = _norm_date(str(row.get(date_col) or ""))
        if not ddate:
            continue
        name = str(row.get(name_col) or "").strip() if name_col else ""
        out[code] = {"delist_date": ddate, "name": name, "reason": "终止上市"}
    return out


def _fetch_sh_delist(ak: Any) -> Dict[str, Dict[str, str]]:
    """拉取上交所退市股票（兼容新旧 API 签名）。"""
    df = None
    for kwargs in ({"symbol": "全部"}, {"indicator": "终止上市公司"}):
        try:
            df = ak.stock_info_sh_delist(**kwargs)
            break
        except Exception:
            continue
    if df is None:
        return {}
    return _normalize_delist_df(
        df,
        code_cols=["SECURITY_CODE_A", "END_SHARE_CODE", "证券代码", "代码"],
        name_cols=["SECURITY_ABBR_A", "COMPANY_ABBR", "证券简称", "名称"],
        date_cols=["CHANGE_DATE", "终止上市日期", "退市日期"],
    )


def _fetch_sz_delist(ak: Any) -> Dict[str, Dict[str, str]]:
    """拉取深交所退市股票（兼容新旧 API 签名）。"""
    df = None
    for kwargs in ({"indicator": "终止上市公司"}, {"symbol": "终止上市公司"}):
        try:
            df = ak.stock_info_sz_delist(**kwargs)
            break
        except Exception:
            continue
    if df is None:
        return {}
    return _normalize_delist_df(
        df,
        code_cols=["证券代码", "代码", "SECURITY_CODE_A"],
        name_cols=["证券简称", "名称", "SECURITY_ABBR_A"],
        date_cols=["终止上市日期", "退市日期", "CHANGE_DATE"],
    )


def fetch_delist_from_akshare() -> Dict[str, Dict[str, str]]:
    """从 AkShare 拉取 A 股退市股票列表。

    使用 ``stock_info_sh_delist`` / ``stock_info_sz_delist``。
    如果 akshare 不可用或拉取失败，返回空字典并记录警告（不抛异常）。

    :return: ``{code: {"delist_date", "name", "reason"}}``，失败时为空字典
    """
    try:
        from skills.common.ak_lock import import_akshare

        ak = import_akshare()
    except Exception:
        logger.warning("akshare 不可用，跳过退市日历拉取")
        return {}

    out: Dict[str, Dict[str, str]] = {}
    try:
        out.update(_fetch_sh_delist(ak))
    except Exception:
        logger.warning("拉取上交所退市列表失败", exc_info=True)
    try:
        out.update(_fetch_sz_delist(ak))
    except Exception:
        logger.warning("拉取深交所退市列表失败", exc_info=True)

    if not out:
        logger.warning("AkShare 退市列表拉取为空")
    return out


def update_delist_calendar(force: bool = False) -> Dict[str, Any]:
    """更新退市日历（合并已有 + 新拉取）。

    :param force: True=强制重新拉取；False=日历文件不存在或超过 30 天才更新
    :return: ``{"updated": bool, "total": 总退市数, "new": 新增数, "path": 文件路径}``
    """
    existing = load_delist_calendar()

    need_update = bool(force)
    if not need_update:
        if not existing:
            need_update = True
        else:
            try:
                mtime = os.path.getmtime(DELIST_CALENDAR_PATH)
            except OSError:
                mtime = 0.0
            if mtime <= 0:
                need_update = True
            else:
                age_days = (datetime.now().timestamp() - mtime) / 86400.0
                need_update = age_days >= _UPDATE_TTL_DAYS

    if not need_update:
        return {
            "updated": False,
            "total": len(existing),
            "new": 0,
            "path": DELIST_CALENDAR_PATH,
        }

    fetched = fetch_delist_from_akshare()
    merged: Dict[str, Dict[str, str]] = dict(existing)
    new_count = 0
    for code, info in fetched.items():
        if code not in merged:
            new_count += 1
        merged[code] = info

    # 拉取失败且已有数据时不重写，避免无谓覆盖
    if fetched or not existing:
        save_delist_calendar(merged)
        updated = True
    else:
        updated = False

    return {
        "updated": updated,
        "total": len(merged),
        "new": new_count,
        "path": DELIST_CALENDAR_PATH,
    }


def enrich_bars_with_delist_flag(
    stock_bars: Dict[str, List[dict]],
    calendar: Optional[Dict] = None,
) -> Dict[str, List[dict]]:
    """给每只股票的 bars 添加 ``delist_date`` 和 ``is_delisted`` 标记。

    仅添加元数据，不截断数据；浅拷贝，不修改原始数据。
    用于回测时标记退市股票。

    :return: 添加了元数据的 stock_bars
    """
    cal = calendar if calendar is not None else load_delist_calendar()
    out: Dict[str, List[dict]] = {}
    for code, bars in (stock_bars or {}).items():
        ddate = delist_date_of(code, cal)
        enriched: List[dict] = []
        for b in (bars or []):
            nb = dict(b)
            nb["delist_date"] = ddate or ""
            nb["is_delisted"] = bool(ddate)
            enriched.append(nb)
        out[code] = enriched
    return out


if __name__ == "__main__":
    # 模拟数据测试
    test_bars = {
        "000001": [{"date": "2020-01-02", "close": 10.0}, {"date": "2020-06-30", "close": 11.0}, {"date": "2020-12-31", "close": 12.0}],
        "000002": [{"date": "2020-01-02", "close": 20.0}, {"date": "2020-06-30", "close": 21.0}],
        "600001": [{"date": "2020-01-02", "close": 5.0}, {"date": "2020-08-20", "close": 0.5}],
    }
    test_calendar = {
        "600001": {"delist_date": "2020-08-20", "name": "TestDelisted", "reason": "测试退市"},
    }
    filtered, adj = filter_survivorship(test_bars, as_of="2020-12-31", calendar=test_calendar, keep_delisted_history=True)
    print(f"filtered codes: {list(filtered.keys())}")
    print(f"adjustments: {adj}")
    for code, bars in filtered.items():
        print(f"  {code}: {len(bars)} bars, last={bars[-1]['date']}")
    # 验证
    report = validate_universe_survivorship(test_bars, start_date="2020-01-01", end_date="2020-12-31", calendar=test_calendar)
    print(f"survivorship report: {report['survivorship_bias_risk']}, delisted_during={report['delisted_during']}")
