"""股票行情 API：统一走腾讯财经 qt.gtimg.cn，带短时缓存。"""


import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

import requests

from core.data_policy import QUOTE_MEM_TTL_SECONDS, QUOTE_STALE_MAX_SECONDS
from core.http_retry import requests_get_with_retry

logger = logging.getLogger(__name__)


class StockAPI:
    TENCENT_URL = "https://qt.gtimg.cn/q="
    # 东方财富 push2 JSON API（回退源，腾讯不可用时启用）
    EM_PUSH_URL = "https://push2.eastmoney.com/api/qt/stock/get"
    EM_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    EM_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f59,f60"
    CACHE_TTL_SECONDS = QUOTE_MEM_TTL_SECONDS
    # 批量路径必须低于 /api/watching/quotes 的 wait_for；超时重试会占满默认线程池
    BATCH_HTTP_TIMEOUT = 4.0
    BATCH_HTTP_RETRIES = 1
    STALE_CACHE_MAX_SECONDS = QUOTE_STALE_MAX_SECONDS

    # 热门名称 → 腾讯行情符号
    STOCK_MAPPING = {
        "阿里巴巴": "usBABA",
        "苹果": "usAAPL",
        "Apple": "usAAPL",
        "谷歌": "usGOOGL",
        "Google": "usGOOGL",
        "微软": "usMSFT",
        "Microsoft": "usMSFT",
        "亚马逊": "usAMZN",
        "Amazon": "usAMZN",
        "特斯拉": "usTSLA",
        "Tesla": "usTSLA",
        "Meta": "usMETA",
        "脸书": "usMETA",
        "Facebook": "usMETA",
        "英伟达": "usNVDA",
        "NVIDIA": "usNVDA",
        "AMD": "usAMD",
        "英特尔": "usINTC",
        "高通": "usQCOM",
        "博通": "usAVGO",
        "京东": "usJD",
        "百度": "usBIDU",
        "网易": "usNTES",
        "拼多多": "usPDD",
        "蔚来": "usNIO",
        "理想汽车": "usLI",
        "小鹏汽车": "usXPEV",
        "台积电": "usTSM",
        "贵州茅台": "sh600519",
        "茅台": "sh600519",
        "五粮液": "sz000858",
        "比亚迪": "sz002594",
        "宁德时代": "sz300750",
        "隆基绿能": "sh601012",
        "招商银行": "sh600036",
        "平安银行": "sz000001",
        "工商银行": "sh601398",
        "建设银行": "sh601939",
        "中国银行": "sh601988",
        "农业银行": "sh601288",
        "中国平安": "sh601318",
        "中国人寿": "sh601628",
        "中信证券": "sh600030",
        "东方财富": "sz300059",
        "同花顺": "sz300033",
        "科大讯飞": "sz002230",
        "海康威视": "sz002415",
        "格力电器": "sz000651",
        "美的集团": "sz000333",
        "恒瑞医药": "sh600276",
        "迈瑞医疗": "sz300760",
        "泸州老窖": "sz000568",
        "山西汾酒": "sh600809",
        "中国移动": "sh600941",
        "中国联通": "sh600050",
        "中国电信": "sh601728",
        "中国石化": "sh600028",
        "中国石油": "sh601857",
        "中国铝业": "sh601600",
        "中铝": "sh601600",
        "腾讯": "hk00700",
        "腾讯控股": "hk00700",
        "美团": "hk03690",
        "小米": "hk01810",
        "快手": "hk01024",
        "快手-W": "hk01024",
    }

    _cache: Dict[str, Tuple[float, dict]] = {}
    _cache_lock = threading.RLock()

    @classmethod
    def clear_cache(cls):
        with cls._cache_lock:
            cls._cache.clear()

    @classmethod
    def resolve_symbol(cls, stock_code: str) -> Optional[str]:
        """将名称/代码解析为腾讯行情符号；无法解析时返回 None。"""
        code = (stock_code or "").strip()
        if not code:
            return None

        if code in cls.STOCK_MAPPING:
            return cls.STOCK_MAPPING[code]

        lower = code.lower()
        if lower in {k.lower(): v for k, v in cls.STOCK_MAPPING.items()}:
            for k, v in cls.STOCK_MAPPING.items():
                if k.lower() == lower:
                    return v

        # 已带市场前缀
        if lower.startswith(("sh", "sz", "hk", "us")):
            if lower.startswith("hk") and not code.lower().startswith("hk"):
                return lower
            if lower.startswith("us"):
                return "us" + code[2:].upper() if code[2:] else None
            return lower

        # 6 位 A 股
        if code.isdigit() and len(code) == 6:
            if code.startswith("6"):
                return f"sh{code}"
            if code.startswith(("0", "3")):
                return f"sz{code}"

        # 港股纯数字（4～5 位）
        if code.isdigit() and 4 <= len(code) <= 5:
            return f"hk{code.zfill(5)}"

        # 美股 ticker（纯字母）
        if code.isalpha() and 1 <= len(code) <= 5:
            return f"us{code.upper()}"

        # 中文名未在映射表
        if any("\u4e00" <= ch <= "\u9fff" for ch in code):
            return None

        return code

    @classmethod
    def query(cls, stock_code: str) -> dict:
        symbol = cls.resolve_symbol(stock_code)
        if not symbol:
            return {
                "success": False,
                "stock_code": stock_code,
                "error": (
                    f"无法识别股票「{stock_code}」。"
                    "请使用股票代码（如 600519、AAPL、00700），或热门中文名称。"
                ),
            }

        with cls._cache_lock:
            cached = cls._cache.get(symbol)
        now = time.time()
        if cached and now - cached[0] < cls.CACHE_TTL_SECONDS:
            result = dict(cached[1])
            result["cached"] = True
            return result

        # 依次尝试：腾讯 → 东方财富（任一成功即返回）
        last_error = "未知错误"
        for source_fn in (cls._query_tencent, cls._query_eastmoney):
            try:
                result = source_fn(stock_code, symbol)
                if result.get("success"):
                    with cls._cache_lock:
                        cls._cache[symbol] = (now, dict(result))
                    return result
                last_error = result.get("error", last_error)
            except Exception as e:
                logger.exception('unexpected error in query')
                last_error = str(e)
                continue

        return {
            "success": False,
            "stock_code": stock_code,
            "error": f"行情查询失败（已尝试多源）: {last_error}",
        }

    @classmethod
    def _fresh_cached(cls, symbol: str, *, now: float) -> Optional[dict]:
        with cls._cache_lock:
            cached = cls._cache.get(symbol)
        if not cached or now - cached[0] >= cls.CACHE_TTL_SECONDS:
            return None
        result = dict(cached[1])
        result["cached"] = True
        return result

    @classmethod
    def _stale_cached(cls, symbol: str, *, now: float) -> Optional[dict]:
        """TTL 过期但仍在窗口内的缓存；上游超时/失败时保底。"""
        with cls._cache_lock:
            cached = cls._cache.get(symbol)
        if not cached:
            return None
        ts, payload = cached
        if now - ts > cls.STALE_CACHE_MAX_SECONDS:
            return None
        if not isinstance(payload, dict) or not payload.get("success"):
            return None
        result = dict(payload)
        result["cached"] = True
        result["stale"] = now - ts >= cls.CACHE_TTL_SECONDS
        return result

    @classmethod
    def batch_query(cls, stock_codes: List[str]) -> Dict[str, dict]:
        """批量查询多只股票行情，一次网络请求。"""
        if not stock_codes:
            return {}

        now = time.time()
        results = {}
        uncached_codes = []
        symbols_map = {}

        # 先检查缓存
        for code in stock_codes:
            symbol = cls.resolve_symbol(code)
            if not symbol:
                results[code] = {
                    "success": False,
                    "stock_code": code,
                    "error": f"无法识别股票「{code}」",
                }
                continue
            symbols_map[code] = symbol
            fresh = cls._fresh_cached(symbol, now=now)
            if fresh:
                results[code] = fresh
            else:
                uncached_codes.append(code)

        # 只查询未缓存的股票
        if not uncached_codes:
            return results

        # 腾讯API支持用逗号分隔多只股票
        symbols = [symbols_map[c] for c in uncached_codes]
        url = f"{cls.TENCENT_URL}{','.join(symbols)}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://finance.qq.com/",
        }

        try:
            response = requests_get_with_retry(
                url,
                headers=headers,
                timeout=cls.BATCH_HTTP_TIMEOUT,
                retries=cls.BATCH_HTTP_RETRIES,
            )
            response.raise_for_status()
            content = response.content.decode("gbk", errors="ignore")

            # 解析多只股票数据：v_sh600519="1~...";v_sz000858="1~...";
            for line in content.split(";"):
                line = line.strip()
                if not line:
                    continue
                # 找到 symbol=... 的格式
                if '="' in line:
                    symbol_part, data_part = line.split('="', 1)
                    symbol = symbol_part.lstrip("v_")
                    data = data_part.rstrip('";\n ')
                    if symbol and "~" in data:
                        # 找到对应的原始代码
                        found_code = None
                        for code, sym in symbols_map.items():
                            if sym == symbol:
                                found_code = code
                                break
                        if found_code:
                            result = cls._parse_tencent_data(found_code, symbol, data)
                            if result.get("success"):
                                with cls._cache_lock:
                                    cls._cache[symbol] = (now, dict(result))
                            results[found_code] = result

            # 确保所有请求的代码都有结果
            for code in uncached_codes:
                if code not in results:
                    results[code] = {
                        "success": False,
                        "stock_code": code,
                        "error": f"无法查询到股票「{code}」的行情信息",
                    }

        except requests.exceptions.RequestException as e:
            for code in uncached_codes:
                results[code] = {
                    "success": False,
                    "stock_code": code,
                    "error": f"股票查询失败: {e}",
                }
        except Exception as e:
            logger.exception('unexpected error in batch_query')
            for code in uncached_codes:
                results[code] = {
                    "success": False,
                    "stock_code": code,
                    "error": f"处理股票数据失败: {e}",
                }

        failed = [c for c in uncached_codes if not results.get(c, {}).get("success")]
        if not failed:
            return results

        # 过期缓存保底（避免整批空白）
        still_failed = []
        for code in failed:
            stale = cls._stale_cached(symbols_map.get(code) or "", now=now)
            if stale:
                results[code] = stale
            else:
                still_failed.append(code)

        # 东方财富 ulist 一次回退整批；禁止再串行逐票（8×超时会拖死线程池）
        if still_failed:
            em_part = cls._query_eastmoney_batch(still_failed, symbols_map)
            for code, em_result in em_part.items():
                if em_result.get("success"):
                    results[code] = em_result

        return results

    @classmethod
    def _parse_tencent_data(cls, stock_code: str, symbol: str, data_str: str) -> dict:
        """解析单条腾讯行情数据字符串。"""
        data = data_str.split("~")
        if len(data) < 45:
            return {
                "success": False,
                "stock_code": stock_code,
                "error": "无法解析股票数据",
            }

        market = cls._market_of(symbol)
        currency = {"CN": "CNY", "HK": "HKD", "US": "USD"}.get(market, "CNY")
        unit = {"CNY": "元", "HKD": "HK$", "USD": "$"}[currency]

        stock_name = data[1] or stock_code
        code_display = data[2] or stock_code

        try:
            price = float(data[3] or 0)
            pre_close = float(data[4] or 0)
            open_price = float(data[5] or 0)
            volume = float(data[6] or 0)
            high = float(data[33] or 0) if len(data) > 33 else 0.0
            low = float(data[34] or 0) if len(data) > 34 else 0.0
            change_amount = float(data[31] or 0) if len(data) > 31 else (price - pre_close)
            change_percent = float(data[32] or 0) if len(data) > 32 else (
                (change_amount / pre_close * 100) if pre_close else 0.0
            )
        except (ValueError, IndexError):
            return {
                "success": False,
                "stock_code": stock_code,
                "error": "无法解析股票数据",
            }

        if price == 0 and pre_close == 0:
            return {
                "success": False,
                "stock_code": stock_code,
                "error": f"无法查询到股票「{stock_code}」的行情信息",
            }

        price_str = cls._fmt_price(price, currency, unit)
        change_amt_str = cls._fmt_price(change_amount, currency, unit, signed=True)

        return {
            "success": True,
            "stock_code": code_display,
            "stock_name": stock_name,
            "symbol": symbol,
            "market": market,
            "price": price_str,
            "price_raw": price,
            "change": f"{change_percent:+.2f}%",
            "change_raw": change_percent,
            "change_amount": change_amt_str,
            "open": cls._fmt_price(open_price, currency, unit) if open_price else "",
            "high": cls._fmt_price(high, currency, unit) if high else "",
            "low": cls._fmt_price(low, currency, unit) if low else "",
            "volume": cls._format_volume(volume, market),
            "volume_raw": volume,
            "market_cap": "",
            "currency": currency,
            "unit": unit,
            "cached": False,
            "description": (
                f"{stock_name}({code_display})最新价{price_str}，"
                f"{change_amt_str}，涨幅{change_percent:+.2f}%"
            ),
        }

    @classmethod
    def _query_tencent(cls, stock_code: str, symbol: str) -> dict:
        url = f"{cls.TENCENT_URL}{symbol}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://finance.qq.com/",
        }
        response = requests_get_with_retry(url, headers=headers, timeout=10, retries=2)
        response.raise_for_status()
        # 腾讯接口常为 GBK
        content = response.content.decode("gbk", errors="ignore")

        if "=~" in content or '=""' in content or not content.strip():
            # 空数据：v_xxx="";
            if '="";' in content or '=""' in content.split(";")[0]:
                return {
                    "success": False,
                    "stock_code": stock_code,
                    "error": f"无法查询到股票「{stock_code}」的行情信息",
                }

        # 格式: v_sh600519="1~贵州茅台~600519~..."
        if "~" not in content:
            return {
                "success": False,
                "stock_code": stock_code,
                "error": f"无法查询到股票「{stock_code}」的行情信息",
            }

        payload = content.split('="', 1)[-1].rstrip('";\n ')
        data = payload.split("~")
        if len(data) < 45:
            return {
                "success": False,
                "stock_code": stock_code,
                "error": "无法解析股票数据",
            }

        market = cls._market_of(symbol)
        currency = {"CN": "CNY", "HK": "HKD", "US": "USD"}.get(market, "CNY")
        unit = {"CNY": "元", "HKD": "HK$", "USD": "$"}[currency]

        stock_name = data[1] or stock_code
        code_display = data[2] or stock_code

        try:
            price = float(data[3] or 0)
            pre_close = float(data[4] or 0)
            open_price = float(data[5] or 0)
            volume = float(data[6] or 0)
            # 腾讯字段：33 最高 34 最低（A股）；部分市场位置一致
            high = float(data[33] or 0) if len(data) > 33 else 0.0
            low = float(data[34] or 0) if len(data) > 34 else 0.0
            # 涨跌额/幅：31 / 32
            change_amount = float(data[31] or 0) if len(data) > 31 else (price - pre_close)
            change_percent = float(data[32] or 0) if len(data) > 32 else (
                (change_amount / pre_close * 100) if pre_close else 0.0
            )
        except (ValueError, IndexError):
            return {
                "success": False,
                "stock_code": stock_code,
                "error": "无法解析股票数据",
            }

        if price == 0 and pre_close == 0:
            return {
                "success": False,
                "stock_code": stock_code,
                "error": f"无法查询到股票「{stock_code}」的行情信息",
            }

        price_str = cls._fmt_price(price, currency, unit)
        change_amt_str = cls._fmt_price(change_amount, currency, unit, signed=True)

        return {
            "success": True,
            "stock_code": code_display,
            "stock_name": stock_name,
            "symbol": symbol,
            "market": market,
            "price": price_str,
            "price_raw": price,
            "change": f"{change_percent:+.2f}%",
            "change_raw": change_percent,
            "change_amount": change_amt_str,
            "open": cls._fmt_price(open_price, currency, unit) if open_price else "",
            "high": cls._fmt_price(high, currency, unit) if high else "",
            "low": cls._fmt_price(low, currency, unit) if low else "",
            "volume": cls._format_volume(volume, market),
            "volume_raw": volume,
            "market_cap": "",
            "currency": currency,
            "unit": unit,
            "cached": False,
            "description": (
                f"{stock_name}({code_display})最新价{price_str}，"
                f"{change_amt_str}，涨幅{change_percent:+.2f}%"
            ),
        }

    # ------------------------------------------------------------------ #
    # 东方财富 push2 JSON API（回退源）
    # ------------------------------------------------------------------ #

    @classmethod
    def _match_em_row_code(
        cls,
        row: dict,
        codes: List[str],
        symbols_map: Dict[str, str],
    ) -> Optional[str]:
        raw = str(row.get("f57") or row.get("f12") or "").strip()
        if not raw:
            return None
        tail = raw.split(".")[-1]
        wanted = set(codes)
        if raw in wanted:
            return raw
        if tail in wanted:
            return tail
        for code in codes:
            sym = symbols_map.get(code) or ""
            body = sym[2:] if len(sym) > 2 else sym
            if body == raw or body == tail:
                return code
        return None

    @classmethod
    def _query_eastmoney_batch(
        cls, codes: List[str], symbols_map: Dict[str, str]
    ) -> Dict[str, dict]:
        """东方财富 ulist 一次拉多票；失败返回 {}。"""
        if not codes:
            return {}
        secids: List[str] = []
        seen: set = set()
        for code in codes:
            sym = symbols_map.get(code)
            if not sym:
                continue
            secid = cls._em_secid(sym)
            if not secid or secid in seen:
                continue
            seen.add(secid)
            secids.append(secid)
        if not secids:
            return {}
        params = {
            "secids": ",".join(secids),
            "fields": cls.EM_FIELDS,
            "_": str(int(time.time() * 1000)),
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
        try:
            response = requests_get_with_retry(
                cls.EM_ULIST_URL,
                params=params,
                headers=headers,
                timeout=cls.BATCH_HTTP_TIMEOUT,
                retries=0,
            )
            response.raise_for_status()
            body = response.json()
        except Exception:
            logger.exception('unexpected error in _query_eastmoney_batch')
            return {}
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return {}
        diff = data.get("diff")
        if isinstance(diff, dict):
            rows = list(diff.values())
        elif isinstance(diff, list):
            rows = diff
        else:
            rows = []
        out: Dict[str, dict] = {}
        now = time.time()
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = cls._match_em_row_code(row, codes, symbols_map)
            if not code:
                continue
            sym = symbols_map.get(code)
            if not sym:
                continue
            parsed = cls._parse_em_data(code, sym, row)
            if parsed.get("success"):
                with cls._cache_lock:
                    cls._cache[sym] = (now, dict(parsed))
                out[code] = parsed
        return out

    @classmethod
    def _em_secid(cls, symbol: str) -> Optional[str]:
        """腾讯符号 → 东方财富 secid。"""
        s = symbol.lower()
        if s.startswith("sh"):
            return f"1.{symbol[2:]}"
        if s.startswith("sz"):
            return f"0.{symbol[2:]}"
        if s.startswith("hk"):
            return f"116.{symbol[2:]}"
        if s.startswith("us"):
            return f"105.{symbol[2:]}"
        return None

    @classmethod
    def _query_eastmoney(cls, stock_code: str, symbol: str) -> dict:
        """东方财富 push2 单股行情（JSON，回退源）。"""
        secid = cls._em_secid(symbol)
        if not secid:
            return {
                "success": False,
                "stock_code": stock_code,
                "error": "东方财富不支持该代码",
            }
        params = {
            "secid": secid,
            "fields": cls.EM_FIELDS,
            "_": str(int(time.time() * 1000)),
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
        response = requests_get_with_retry(
            cls.EM_PUSH_URL, params=params, headers=headers, timeout=8, retries=2
        )
        response.raise_for_status()
        body = response.json()
        data = body.get("data")
        if not data or not isinstance(data, dict):
            return {
                "success": False,
                "stock_code": stock_code,
                "error": f"无法查询到股票「{stock_code}」的行情信息",
            }
        return cls._parse_em_data(stock_code, symbol, data)

    @classmethod
    def _parse_em_data(cls, stock_code: str, symbol: str, data: dict) -> dict:
        """解析东方财富 push2 JSON 字段。

        注意：push2 返回的价格为整数（×10^decimals），需按小数位还原。
        """
        market = cls._market_of(symbol)
        # f59 = 小数位数；缺失时按市场默认（CN=2, HK=3, US=2）
        _default_decimals = {"CN": 2, "HK": 3, "US": 2}.get(market, 2)
        try:
            decimals = int(data.get("f59") or _default_decimals)
            if not (1 <= decimals <= 6):
                decimals = _default_decimals
        except (ValueError, TypeError):
            decimals = _default_decimals
        divisor = 10 ** decimals

        try:
            price = float(data.get("f43") or 0) / divisor
            pre_close = float(data.get("f60") or 0) / divisor
            open_price = float(data.get("f46") or 0) / divisor
            high = float(data.get("f44") or 0) / divisor
            low = float(data.get("f45") or 0) / divisor
            volume = float(data.get("f47") or 0)
        except (ValueError, TypeError):
            return {
                "success": False,
                "stock_code": stock_code,
                "error": "东方财富数据解析失败",
            }

        if price == 0 and pre_close == 0:
            return {
                "success": False,
                "stock_code": stock_code,
                "error": f"无法查询到股票「{stock_code}」的行情信息",
            }

        currency = {"CN": "CNY", "HK": "HKD", "US": "USD"}.get(market, "CNY")
        unit = {"CNY": "元", "HKD": "HK$", "USD": "$"}[currency]

        stock_name = data.get("f58") or stock_code
        code_display = data.get("f57") or stock_code
        change_amount = price - pre_close
        change_percent = (
            (change_amount / pre_close * 100) if pre_close else 0.0
        )

        price_str = cls._fmt_price(price, currency, unit)
        change_amt_str = cls._fmt_price(change_amount, currency, unit, signed=True)

        return {
            "success": True,
            "stock_code": code_display,
            "stock_name": stock_name,
            "symbol": symbol,
            "market": market,
            "price": price_str,
            "price_raw": price,
            "change": f"{change_percent:+.2f}%",
            "change_raw": change_percent,
            "change_amount": change_amt_str,
            "open": cls._fmt_price(open_price, currency, unit) if open_price else "",
            "high": cls._fmt_price(high, currency, unit) if high else "",
            "low": cls._fmt_price(low, currency, unit) if low else "",
            "volume": cls._format_volume(volume, market),
            "volume_raw": volume,
            "market_cap": "",
            "currency": currency,
            "unit": unit,
            "cached": False,
            "description": (
                f"{stock_name}({code_display})最新价{price_str}，"
                f"{change_amt_str}，涨幅{change_percent:+.2f}%"
            ),
        }

    @staticmethod
    def _market_of(symbol: str) -> str:
        s = symbol.lower()
        if s.startswith(("sh", "sz")):
            return "CN"
        if s.startswith("hk"):
            return "HK"
        if s.startswith("us"):
            return "US"
        return "CN"

    @staticmethod
    def _fmt_price(value: float, currency: str, unit: str, signed: bool = False) -> str:
        if currency == "CNY":
            return f"{value:+.2f}{unit}" if signed else f"{value:.2f}{unit}"
        # $ / HK$ 前缀
        if signed:
            sign = "+" if value >= 0 else "-"
            return f"{sign}{unit}{abs(value):.2f}"
        return f"{unit}{value:.2f}"

    @staticmethod
    def _format_volume(volume: float, market: str) -> str:
        # A 股字段多为手；美股/港股多为股。统一展示为「万」量级可读字符串。
        if volume <= 0:
            return ""
        if volume >= 1e8:
            return f"{volume / 1e8:.2f}亿"
        if volume >= 1e4:
            return f"{volume / 1e4:.2f}万"
        return f"{volume:.0f}"
