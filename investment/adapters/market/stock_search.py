"""股票名称/代码搜索（关注列表用）。

搜索路径：热门映射 → 曾用名/别名 →（像代码时）行情 → 本地现货缓存 → A 股代码名称索引。
索引首次拉取可能较慢，之后读本地；避免每次中文搜索打全市场现货。
曾用名：代码表只有现名（如中直股份），用户常搜旧名（哈飞股份）→ 靠 SEARCH_ALIASES。
"""


import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from adapters.market.quote_api import StockAPI

# 进程内精简索引：(code, name)，避免每次扫原始现货大表字段
_SPOT_PAIRS: Optional[List[Tuple[str, str]]] = None
_SPOT_PAIRS_TS = 0.0
_SPOT_PAIRS_TTL = 1800.0  # 30 分钟

_CODE_NAME_PAIRS: Optional[List[Tuple[str, str]]] = None
_CODE_NAME_TS = 0.0
_CODE_NAME_TTL = 86400.0  # 24 小时
_CODE_NAME_FETCHING = False

# 曾用名 / 俗称 → (现代码, 现简称)。优先于代码表现名匹配。
# 来源：交易所更名历史（如 stock_info_change_name）；按需增补，勿塞全市场。
SEARCH_ALIASES: Dict[str, Tuple[str, str]] = {
    # 600038 中直股份
    "哈飞": ("600038", "中直股份"),
    "哈飞股份": ("600038", "中直股份"),
    "G哈飞": ("600038", "中直股份"),
    # 600072 中船科技（曾用名含「中船股份」）
    "中船股份": ("600072", "中船科技"),
    "江南重工": ("600072", "中船科技"),
    "钢构工程": ("600072", "中船科技"),
    "中船钢构": ("600072", "中船科技"),
    # 600150 中国船舶
    "沪东重机": ("600150", "中国船舶"),
    "中国船舶工业": ("600150", "中国船舶"),
    # 常见军工 / 简称
    "中航沈飞": ("600760", "中航沈飞"),
    "沈飞": ("600760", "中航沈飞"),
    "中航西飞": ("000768", "中航西飞"),
    "西飞": ("000768", "中航西飞"),
    "洪都": ("600316", "洪都航空"),
    "航发动力": ("600893", "航发动力"),
    "中航机电": ("600372", "中航机载"),
}

_CORP_SUFFIX_RE = re.compile(
    r"(股份有限公司|有限公司|集团股份|股份|集团)$"
)


def _code_name_path() -> str:
    from core.paths import DATA_DIR

    return os.path.join(DATA_DIR, "store", "a_code_name.json")


def _bare_from_symbol(symbol: str) -> str:
    s = (symbol or "").strip()
    low = s.lower()
    if low.startswith(("sh", "sz")) and len(s) >= 8:
        return s[2:]
    if low.startswith("hk") and len(s) > 2:
        return s[2:].zfill(5) if s[2:].isdigit() else s[2:]
    if low.startswith("us") and len(s) > 2:
        return s[2:].upper()
    return s


def _add_item(
    items: List[dict],
    seen: Set[str],
    *,
    code: str,
    name: str,
    market: str = "",
    hint: str = "",
) -> None:
    c = str(code or "").strip()
    if not c:
        return
    key = c.upper() if c.isalpha() else c
    if key in seen:
        return
    seen.add(key)
    items.append(
        {
            "stock_code": c,
            "stock_name": str(name or "").strip() or c,
            "market": market or "",
            "hint": hint or "",
        }
    )


def _row_code_name(row: dict) -> Tuple[str, str]:
    code = str(
        row.get("code") or row.get("股票代码") or row.get("代码") or ""
    ).strip()
    name = str(
        row.get("name") or row.get("股票名称") or row.get("名称") or ""
    ).strip()
    return code, name


def _spot_pairs_cheap() -> List[Tuple[str, str]]:
    """只读内存/磁盘现货，绝不触发 live 拉取（避免搜索卡顿）。"""
    global _SPOT_PAIRS, _SPOT_PAIRS_TS
    now = time.time()
    if _SPOT_PAIRS is not None and now - _SPOT_PAIRS_TS < _SPOT_PAIRS_TTL:
        return _SPOT_PAIRS

    rows: List[dict] = []
    try:
        from adapters.screen import engine as screen_engine

        mem = getattr(screen_engine, "_SPOT_MEM", None)
        ttl = float(getattr(screen_engine, "_SPOT_MEM_TTL_SEC", 120) or 120)
        if mem and now - float(mem[0]) < ttl:
            rows = list(mem[1] or [])
        if not rows:
            loader = getattr(screen_engine, "_load_disk_spot", None)
            if callable(loader):
                disk = loader()
                if disk:
                    rows = list(disk)
    except Exception:
        logger.exception('unexpected error in _spot_pairs_cheap')
        rows = []

    pairs: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        code, name = _row_code_name(row)
        if not code:
            continue
        if code in seen:
            continue
        seen.add(code)
        pairs.append((code, name or code))

    _SPOT_PAIRS = pairs
    _SPOT_PAIRS_TS = now
    return pairs


def _load_code_name_disk() -> List[Tuple[str, str]]:
    path = _code_name_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        fetched = float(payload.get("fetched_at") or 0)
        if time.time() - fetched > _CODE_NAME_TTL * 7:
            return []
        rows = payload.get("pairs") or []
        out: List[Tuple[str, str]] = []
        for it in rows:
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                out.append((str(it[0]), str(it[1])))
        return out
    except Exception:
        logger.exception('unexpected error in _load_code_name_disk')
        return []


def _save_code_name_disk(pairs: List[Tuple[str, str]]) -> None:
    path = _code_name_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "fetched_at": time.time(),
                    "count": len(pairs),
                    "pairs": [[c, n] for c, n in pairs],
                },
                f,
                ensure_ascii=False,
            )
    except Exception:
        logger.exception('unexpected error in _save_code_name_disk')



import logging

logger = logging.getLogger(__name__)
def _fetch_code_name_live() -> List[Tuple[str, str]]:
    from adapters.market.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_info_a_code_name", None)
    if not callable(fn):
        return []
    df = fn()
    pairs: List[Tuple[str, str]] = []
    if df is None or getattr(df, "empty", True):
        return pairs
    cols = {str(c).lower(): c for c in df.columns}
    code_col = cols.get("code") or cols.get("代码") or list(df.columns)[0]
    name_col = cols.get("name") or cols.get("名称") or list(df.columns)[1]
    for _, row in df.iterrows():
        code = str(row.get(code_col) or "").strip()
        name = str(row.get(name_col) or "").strip()
        if code:
            pairs.append((code, name or code))
    return pairs


def _code_name_pairs(*, allow_fetch: bool = True) -> List[Tuple[str, str]]:
    """A 股代码-名称索引：内存 → 磁盘 →（可选）AkShare 拉一次。"""
    global _CODE_NAME_PAIRS, _CODE_NAME_TS, _CODE_NAME_FETCHING
    now = time.time()
    if _CODE_NAME_PAIRS is not None and now - _CODE_NAME_TS < _CODE_NAME_TTL:
        return _CODE_NAME_PAIRS

    disk = _load_code_name_disk()
    if disk:
        _CODE_NAME_PAIRS = disk
        _CODE_NAME_TS = now
        return disk

    if not allow_fetch or _CODE_NAME_FETCHING:
        return disk

    _CODE_NAME_FETCHING = True
    try:
        live = _fetch_code_name_live()
        if live:
            _save_code_name_disk(live)
            _CODE_NAME_PAIRS = live
            _CODE_NAME_TS = now
            return live
    except Exception:
        logger.exception('unexpected error in _code_name_pairs')
    finally:
        _CODE_NAME_FETCHING = False
    return []


def _looks_like_code(q: str) -> bool:
    if q.isdigit() and 4 <= len(q) <= 6:
        return True
    low = q.lower()
    if low.startswith(("sh", "sz", "hk", "us")) and len(q) >= 5:
        return True
    if q.isalpha() and 1 <= len(q) <= 5:
        return True
    return False


def _has_cjk(q: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in q)


def _query_variants(q: str) -> List[str]:
    """生成匹配变体：原串 + 去掉公司后缀（哈飞股份→哈飞；中船股份→中船）。"""
    out: List[str] = []
    seen: Set[str] = set()
    cur = (q or "").strip()
    while cur and cur not in seen:
        seen.add(cur)
        out.append(cur)
        nxt = _CORP_SUFFIX_RE.sub("", cur).strip()
        if nxt == cur:
            break
        cur = nxt
    return out


def _match_text(hay: str, needles: List[str]) -> bool:
    h = hay or ""
    h_lower = h.lower()
    for n in needles:
        if not n:
            continue
        if n in h or n.lower() in h_lower:
            return True
    return False


def _match_pairs(
    pairs: List[Tuple[str, str]],
    needles: List[str],
    items: List[dict],
    seen: Set[str],
    *,
    limit: int,
    hint: str,
) -> None:
    for code, name in pairs:
        if len(items) >= limit:
            return
        if _match_text(code, needles) or _match_text(name, needles):
            _add_item(items, seen, code=code, name=name, market="CN", hint=hint)


def _match_aliases(
    needles: List[str],
    items: List[dict],
    seen: Set[str],
    *,
    limit: int,
) -> None:
    """曾用名/俗称：长别名优先，避免「哈飞」抢在「哈飞股份」前但结果相同。"""
    # (alias_len desc, alias, code, name)
    ranked = sorted(
        ((len(alias), alias, code, name) for alias, (code, name) in SEARCH_ALIASES.items()),
        key=lambda x: (-x[0], x[1]),
    )
    for _, alias, code, name in ranked:
        if len(items) >= limit:
            return
        # 查询命中别名，或别名命中查询（搜「哈飞股份有限公司」仍能落到哈飞）
        hit = False
        for n in needles:
            if not n or len(n) < 2:
                continue
            if n in alias or alias in n:
                hit = True
                break
        if hit:
            _add_item(
                items,
                seen,
                code=code,
                name=name,
                market="CN",
                hint=f"曾用名·{alias}" if alias != name else "别名",
            )


def search_stocks(query: str, limit: int = 8) -> Dict[str, Any]:
    """按名称或代码模糊搜索候选。"""
    q = (query or "").strip()
    limit = max(1, min(int(limit or 8), 20))
    if not q:
        return {"success": True, "query": q, "items": []}

    items: List[dict] = []
    seen: Set[str] = set()
    needles = _query_variants(q)
    q_lower = q.lower()

    # 1) 内置热门名称映射（纯内存，毫秒级）
    for name, symbol in StockAPI.STOCK_MAPPING.items():
        if (
            _match_text(name, needles)
            or q_lower in str(symbol).lower()
            or any(n in _bare_from_symbol(symbol) for n in needles if n.isdigit())
        ):
            bare = _bare_from_symbol(symbol)
            market = StockAPI._market_of(symbol)
            _add_item(items, seen, code=bare, name=name, market=market, hint="热门")
            if len(items) >= limit:
                return {"success": True, "query": q, "items": items, "fast": True}

    # 1b) 曾用名 / 俗称（军工更名等）
    if len(items) < limit:
        _match_aliases(needles, items, seen, limit=limit)
        if len(items) >= limit:
            return {"success": True, "query": q, "items": items, "fast": True}

    # 2) 仅当输入像代码时才打行情（避免中文模糊搜索每次请求外网）
    if _looks_like_code(q) and len(items) < limit:
        try:
            quote = StockAPI.query(q)
        except Exception:
            logger.exception('unexpected error in search_stocks')
            quote = {"success": False}
        if quote.get("success") and quote.get("stock_code"):
            _add_item(
                items,
                seen,
                code=str(quote["stock_code"]),
                name=str(quote.get("stock_name") or q),
                market=str(quote.get("market") or ""),
                hint="行情",
            )
            if len(items) >= limit:
                return {"success": True, "query": q, "items": items, "fast": True}

    # 3) 本地现货缓存模糊（不触发 live）
    if len(items) < limit:
        _match_pairs(
            _spot_pairs_cheap(), needles, items, seen, limit=limit, hint="现货"
        )

    # 4) A 股代码-名称索引（磁盘缓存；中文名且仍无结果时允许首次拉取）
    if len(items) < limit and (_has_cjk(q) or len(q) >= 2):
        allow_fetch = _has_cjk(q) and len(items) == 0
        _match_pairs(
            _code_name_pairs(allow_fetch=allow_fetch),
            needles,
            items,
            seen,
            limit=limit,
            hint="代码表",
        )

    note = ""
    if not items:
        note = (
            "未匹配到股票。可试 6 位代码（如 601600）；"
            "中文名依赖热门映射、曾用名或本地代码表（首次可能需拉取索引）。"
        )

    return {
        "success": True,
        "query": q,
        "items": items[:limit],
        "fast": True,
        "note": note,
    }
