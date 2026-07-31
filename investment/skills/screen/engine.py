"""A 股条件选股：AkShare 拉数 + 纯 Python 过滤（便于离线单测）。"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 常见行业关键词 → 名称模糊匹配用词
SECTOR_ALIASES = {
    "银行": ["银行"],
    "白酒": ["酒", "茅台", "五粮液", "泸州", "汾酒", "洋河", "古井", "今世缘", "酒鬼"],
    "白酒股": ["酒", "茅台", "五粮液", "泸州", "汾酒", "洋河"],
    "新能源": ["新能源", "电池", "锂电", "光伏", "风电", "储能", "宁德", "比亚迪", "隆基"],
    "医药": ["医药", "生物", "制药", "医疗", "药"],
    "证券": ["证券", "券商"],
    "保险": ["保险", "人寿", "太保", "平安"],
    "白酒行业": ["酒"],
    "消费": ["白酒", "食品", "饮料", "乳业", "家电", "零售"],
    "科技": ["软件", "芯片", "半导体", "电子", "通信", "计算机"],
}

# 东方财富 spot 列名 → 内部字段
COLUMN_ALIASES = {
    "code": ("代码", "股票代码", "code"),
    "name": ("名称", "股票名称", "name"),
    "price": ("最新价", "price"),
    "change": ("涨跌幅", "change"),
    "pe": ("市盈率-动态", "市盈率", "市盈率动态", "pe"),
    "pb": ("市净率", "pb"),
    "volume": ("成交量", "volume"),
    "market_cap": ("总市值", "market_cap"),
    "industry": ("所属行业", "行业", "板块", "industry"),
}

# 进程内短缓存，避免同一刷新里 S2/S3 各打一次远端
_SPOT_MEM: Optional[Tuple[float, List[dict]]] = None
_SPOT_MEM_TTL_SEC = 120.0
_SPOT_FETCH_RETRIES = 3
_SPOT_DISK_MAX_AGE_HOURS = 24.0


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            if value != value:  # NaN
                return None
        except Exception:
            pass
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "None", "nan", "NaN"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row_get(row: Dict[str, Any], field: str) -> Any:
    for key in COLUMN_ALIASES.get(field, (field,)):
        if key in row:
            return row[key]
    return None


def _normalize_rows(rows: Sequence[Any]) -> List[dict]:
    """将 list[dict] 或 DataFrame 转为统一 dict 列表。"""
    if rows is None:
        return []

    # pandas DataFrame duck-typing
    if hasattr(rows, "to_dict") and hasattr(rows, "columns"):
        try:
            return rows.to_dict(orient="records")
        except Exception:
            pass

    if isinstance(rows, dict):
        # 单行
        return [rows]

    result = []
    for item in rows:
        if isinstance(item, dict):
            result.append(item)
        else:
            result.append(dict(item))
    return result


def filter_stocks(
    data: Any,
    *,
    sector: Optional[str] = None,
    pe_max: Optional[float] = None,
    pe_min: Optional[float] = None,
    pb_max: Optional[float] = None,
    change_min: Optional[float] = None,
    change_max: Optional[float] = None,
    limit: int = 10,
) -> List[dict]:
    """对现货数据做条件过滤。"""
    rows = _normalize_rows(data)
    if not rows:
        return []

    # 检查必要字段是否存在于至少一行
    sample = rows[0]
    if _row_get(sample, "code") is None or _row_get(sample, "name") is None:
        raise ValueError(f"数据缺少必要列，样例键: {list(sample.keys())}")

    filtered: List[dict] = []
    keywords = SECTOR_ALIASES.get(sector.strip(), [sector.strip()]) if sector else None

    for row in rows:
        name = str(_row_get(row, "name") or "")
        code = str(_row_get(row, "code") or "")
        if not code or not name:
            continue
        if "ST" in name:
            continue

        if keywords:
            industry = str(_row_get(row, "industry") or "")
            haystack = name + industry
            if not any(kw in haystack for kw in keywords):
                continue

        price = _to_float(_row_get(row, "price"))
        change = _to_float(_row_get(row, "change"))
        pe = _to_float(_row_get(row, "pe"))
        pb = _to_float(_row_get(row, "pb"))

        if pe_max is not None:
            if pe is None or pe <= 0 or pe > pe_max:
                continue
        if pe_min is not None:
            if pe is None or pe < pe_min:
                continue
        if pb_max is not None:
            if pb is None or pb <= 0 or pb > pb_max:
                continue
        if change_min is not None:
            if change is None or change < change_min:
                continue
        if change_max is not None:
            if change is None or change > change_max:
                continue

        item = {
            "stock_code": code,
            "stock_name": name,
            "price": price,
            "change": change,
            "pe": pe,
            "pb": pb,
        }
        volume = _row_get(row, "volume")
        market_cap = _row_get(row, "market_cap")
        if volume is not None:
            item["volume"] = volume
        if market_cap is not None:
            item["market_cap"] = market_cap
        filtered.append(item)

    filtered.sort(key=lambda x: (x.get("change") is None, -(x.get("change") or 0)))
    limit = max(1, min(int(limit or 10), 20))
    return filtered[:limit]


def _spot_disk_path() -> str:
    from core.paths import STORE_DIR

    return os.path.join(STORE_DIR, "spot_a_em.json")


def clear_spot_cache() -> None:
    """测试/运维：清空进程内现货缓存。"""
    global _SPOT_MEM
    _SPOT_MEM = None


def _load_disk_spot(max_age_hours: float = _SPOT_DISK_MAX_AGE_HOURS) -> Optional[List[dict]]:
    path = _spot_disk_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        fetched_at = float(payload.get("fetched_at") or 0)
        if fetched_at <= 0:
            return None
        age_h = (time.time() - fetched_at) / 3600.0
        if age_h > max_age_hours:
            return None
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            return None
        return rows
    except Exception:
        return None


def load_disk_spot(max_age_hours: float = _SPOT_DISK_MAX_AGE_HOURS) -> Optional[List[dict]]:
    """公开只读：本地现货缓存行（不触发远端）。"""
    return _load_disk_spot(max_age_hours)


def spot_row_get(row: Dict[str, Any], field: str) -> Any:
    """公开：按字段别名取现货行值。"""
    return _row_get(row, field)


def spot_to_float(value: Any) -> Optional[float]:
    """公开：现货数值解析。"""
    return _to_float(value)


def _save_disk_spot(rows: List[dict]) -> None:
    path = _spot_disk_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"fetched_at": time.time(), "count": len(rows), "rows": rows},
                f,
                ensure_ascii=False,
            )
    except Exception:
        pass


def _fetch_a_spot_live() -> List[dict]:
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()

    last_err: Optional[Exception] = None
    for attempt in range(_SPOT_FETCH_RETRIES):
        try:
            df = ak.stock_zh_a_spot_em()
            rows = _normalize_rows(df)
            if not rows:
                raise RuntimeError("现货表为空")
            return rows
        except Exception as e:
            last_err = e
            if attempt + 1 < _SPOT_FETCH_RETRIES:
                time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(str(last_err) if last_err else "未知错误")


def fetch_a_spot(*, force: bool = False) -> List[dict]:
    """拉取 A 股现货：内存短缓存 → 远端重试 → 磁盘回退。"""
    global _SPOT_MEM
    now = time.time()
    if (
        not force
        and _SPOT_MEM is not None
        and now - _SPOT_MEM[0] < _SPOT_MEM_TTL_SEC
        and _SPOT_MEM[1]
    ):
        return list(_SPOT_MEM[1])

    try:
        rows = _fetch_a_spot_live()
        _SPOT_MEM = (now, rows)
        _save_disk_spot(rows)
        fetch_a_spot.last_source = "live"  # type: ignore[attr-defined]
        return list(rows)
    except Exception as live_err:
        disk = None if force else _load_disk_spot()
        if disk:
            _SPOT_MEM = (now, disk)
            fetch_a_spot.last_source = "disk_cache"  # type: ignore[attr-defined]
            fetch_a_spot.last_error = str(live_err)  # type: ignore[attr-defined]
            return list(disk)
        fetch_a_spot.last_source = "failed"  # type: ignore[attr-defined]
        raise RuntimeError(f"获取行情数据失败: {live_err}") from live_err


class StockScreener:
    def screen(self, params: dict) -> dict:
        market = (params.get("market") or "A").upper()
        if market not in ("A", "A股", "CN", "中国"):
            return {
                "success": False,
                "error": "当前仅支持 A 股筛选（market=A）",
            }

        sector = params.get("sector")
        pe_max = params.get("pe_max")
        pe_min = params.get("pe_min")
        pb_max = params.get("pb_max")
        change_min = params.get("change_min")
        change_max = params.get("change_max")
        limit = params.get("limit", 10)

        has_filter = any(
            v is not None and v != ""
            for v in (sector, pe_max, pe_min, pb_max, change_min, change_max)
        )
        if not has_filter:
            return {
                "success": False,
                "error": "请至少提供一个筛选条件（如 sector、pe_max、change_min）",
            }

        try:
            rows = fetch_a_spot()
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

        try:
            items = filter_stocks(
                rows,
                sector=sector,
                pe_max=_to_float(pe_max) if pe_max is not None else None,
                pe_min=_to_float(pe_min) if pe_min is not None else None,
                pb_max=_to_float(pb_max) if pb_max is not None else None,
                change_min=_to_float(change_min) if change_min is not None else None,
                change_max=_to_float(change_max) if change_max is not None else None,
                limit=limit if limit is not None else 10,
            )
        except Exception as e:
            return {"success": False, "error": f"筛选失败: {e}"}

        filters = {
            "sector": sector,
            "pe_max": pe_max,
            "pe_min": pe_min,
            "pb_max": pb_max,
            "change_min": change_min,
            "change_max": change_max,
            "limit": limit,
        }
        source = getattr(fetch_a_spot, "last_source", "live")

        if not items:
            out = {
                "success": True,
                "count": 0,
                "stocks": [],
                "message": "未找到符合条件的股票，可尝试放宽 PE/PB 或行业条件。",
                "filters": filters,
                "data_source": source,
            }
            if source == "disk_cache":
                out["message"] += "（现货来自本地缓存，行情可能偏旧）"
            return out

        out = {
            "success": True,
            "count": len(items),
            "stocks": items,
            "filters": filters,
            "data_source": source,
            "note": "以上为条件筛选结果，市场有风险，不保证收益，不代客下单。",
        }
        if source == "disk_cache":
            out["note"] = "现货来自本地缓存（远端拉取失败），数据可能偏旧。" + out["note"]
        return out
