"""MarketPorts：类型化 Port 协议 + 默认适配器组装（DS encapsulate B）。

实现侧仍走 core.ports.registry / adapters.bind；此处只定契约。
"""


import logging

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple


class QuotePort(Protocol):
    def query(self, code: str) -> dict: ...

    def batch_query(self, codes: Sequence[str]) -> Dict[str, dict]: ...


class BarsPort(Protocol):
    def fetch_daily(
        self,
        code: str,
        *,
        limit: int = 120,
        use_cache: bool = True,
        cache_max_age_hours: float = 24.0,
        incremental: bool = True,
        adjust: str = "qfq",
        offline_ok: bool = False,
        offline_only: bool = False,
    ) -> Tuple[List[dict], str]: ...


class IndexBarsPort(Protocol):
    def fetch_index(
        self, code: str, *, limit: int = 120
    ) -> Tuple[List[dict], str]: ...


class MinuteBarsPort(Protocol):
    def fetch_minute(self, code: str, **kwargs: Any) -> Any: ...


class FundamentalsPort(Protocol):
    def build(self, code: str, **kwargs: Any) -> Any: ...


class NewsPort(Protocol):
    def build(self, code: str, *, limit: int = 8, **kwargs: Any) -> Any: ...


class SpotPort(Protocol):
    def fetch(self, *, force: bool = False) -> List[dict]: ...

    def load_disk(self, *, max_age_hours: float) -> Optional[List[dict]]: ...


class SnapshotStorePort(Protocol):
    def load(
        self, kind: str, code: str, *, max_age_hours: float
    ) -> Optional[Tuple[Any, Dict[str, Any]]]: ...

    def save(
        self, kind: str, code: str, data: Any, *, data_source: str, as_of: Optional[str] = None
    ) -> str: ...


@dataclass
class MarketPorts:
    quote: QuotePort
    bars: BarsPort
    index: IndexBarsPort
    minute: MinuteBarsPort
    fundamentals: FundamentalsPort
    news: NewsPort
    spot: SpotPort
    snapshots: SnapshotStorePort


class _QuoteAdapter:
    def query(self, code: str) -> dict:
        from core.ports.market import query_quote

        return query_quote(code)

    def batch_query(self, codes: Sequence[str]) -> Dict[str, dict]:
        from core.ports.market import batch_query_quotes

        return batch_query_quotes(codes)


class _BarsAdapter:
    def fetch_daily(self, code: str, **kwargs: Any) -> Tuple[List[dict], str]:
        from core.ports.market import fetch_daily_bars

        result = fetch_daily_bars(code, **kwargs)
        if isinstance(result, tuple) and len(result) >= 2:
            return list(result[0] or []), str(result[1] or "")
        return list(result or []), "unknown"


class _IndexAdapter:
    def fetch_index(self, code: str, *, limit: int = 120) -> Tuple[List[dict], str]:
        from core.ports.market import fetch_index_bars

        packed = fetch_index_bars(code, limit=int(limit or 120))
        if isinstance(packed, tuple) and len(packed) >= 2:
            return list(packed[0] or []), str(packed[1] or "index")
        return list(packed or []), "index"


class _MinuteAdapter:
    def fetch_minute(self, code: str, **kwargs: Any) -> Any:
        from core.ports.market import fetch_minute_bars

        return fetch_minute_bars(code, **kwargs)


class _FundamentalsAdapter:
    def build(self, code: str, **kwargs: Any) -> Any:
        from core.ports.market import build_fundamentals

        return build_fundamentals(code, **kwargs)


class _NewsAdapter:
    def build(self, code: str, *, limit: int = 8, **kwargs: Any) -> Any:
        from core.ports.market import build_news

        return build_news(code, limit=limit, **kwargs)


class _SpotAdapter:
    def fetch(self, *, force: bool = False) -> List[dict]:
        from core.ports.market import fetch_a_spot

        return list(fetch_a_spot(force=bool(force)) or [])

    def load_disk(self, *, max_age_hours: float) -> Optional[List[dict]]:
        from core.ports.market import load_disk_spot

        return load_disk_spot(max_age_hours=float(max_age_hours))

    @property
    def last_source(self) -> Optional[str]:
        try:
            from core.ports.registry import get_adapter

            impl = get_adapter("fetch_a_spot")
            if impl is not None:
                return getattr(impl, "last_source", None)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in ports.py", exc_info=True)
            pass
        return None


class _SnapshotStoreAdapter:
    def load(
        self, kind: str, code: str, *, max_age_hours: float
    ) -> Optional[Tuple[Any, Dict[str, Any]]]:
        from core.store import load_snapshot_cache

        return load_snapshot_cache(kind, code, max_age_hours=max_age_hours)

    def save(
        self,
        kind: str,
        code: str,
        data: Any,
        *,
        data_source: str,
        as_of: Optional[str] = None,
    ) -> str:
        from core.store import save_snapshot_cache

        return save_snapshot_cache(
            kind, code, data, data_source=data_source, as_of=as_of
        )


def default_ports() -> MarketPorts:
    """组装默认 Port（延迟绑定 skills）。"""
    return MarketPorts(
        quote=_QuoteAdapter(),
        bars=_BarsAdapter(),
        index=_IndexAdapter(),
        minute=_MinuteAdapter(),
        fundamentals=_FundamentalsAdapter(),
        news=_NewsAdapter(),
        spot=_SpotAdapter(),
        snapshots=_SnapshotStoreAdapter(),
    )
