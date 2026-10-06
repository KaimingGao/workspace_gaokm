"""MarketDataService / ResearchDataService（DS encapsulate C）。"""


import logging

logger = logging.getLogger(__name__)
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import threading

from core.data.gate import (
    DEFAULT_ADJUST_POLICY,
    allows_production_score,
    infer_adjust,
    normalize_adjust_policy,
)
from core.data.ports import MarketPorts, default_ports
from core.data.types import BarsResult, DataEnvelope, PitMeta, QualityMeta
from core.data.policy import (
    DAILY_CACHE_HOURS,
    FUNDAMENTALS_CACHE_HOURS,
    MINUTE_CACHE_HOURS,
    NEWS_CACHE_HOURS,
)
from core.store import assess_quality

_METRICS: Dict[str, int] = {
    "bars_ok": 0,
    "bars_empty": 0,
    "bars_fallback": 0,
    "bars_rejected_fallback": 0,
    "bars_offline_miss": 0,
    "pool_worker_failed": 0,
}
# 与舆情 TTL（data/store/news/{code}.json）分目录，避免互相覆盖后读成空标题
_NEWS_SNAPSHOT_KIND = "news_snap"
_METRICS_LOCK = threading.Lock()


def _bump_metric(key: str, n: int = 1) -> None:
    with _METRICS_LOCK:
        _METRICS[key] = int(_METRICS.get(key) or 0) + int(n)


def metrics_snapshot() -> Dict[str, int]:
    with _METRICS_LOCK:
        return dict(_METRICS)


def reset_metrics() -> None:
    with _METRICS_LOCK:
        for k in list(_METRICS.keys()):
            _METRICS[k] = 0


def bars_pack_worker(code: str, **kw: Any) -> Dict[str, Any]:
    """进程池 worker：须为顶层可 pickle；经默认 Service 返回 dict 信封。"""
    return get_default_service().get_bars(code, **kw).as_dict()


def research_bars_pack_worker(code: str, **kw: Any) -> Dict[str, Any]:
    """研究进程池 worker：默认拒 quote_fallback。"""
    kw.setdefault("reject_quote_fallback", True)
    return get_research_service().get_bars(code, **kw).as_dict()


class MarketDataService:
    """生产默认读口：组信封、PIT、质量门禁；委托 MarketPorts。"""

    def __init__(self, ports: Optional[MarketPorts] = None) -> None:
        self.ports = ports or default_ports()

    def get_quote(self, code: str) -> DataEnvelope:
        raw = str(code or "").strip()
        quote = self.ports.quote.query(raw)
        q = quote if isinstance(quote, dict) else {}
        ok = bool(q.get("success"))
        data = {
            **q,
            "stock_code_query": raw,
            "note": "现价快照；非历史 PIT 面板。快因子 Z@open 经此取 open/昨收。",
        }
        return DataEnvelope(
            kind="quote",
            code=raw,
            ok=ok,
            data=data,
            data_source="tencent_quote" if ok else "empty",
            fetched_at=datetime.now().isoformat(timespec="seconds"),
            quality=QualityMeta(level="snapshot" if ok else "empty"),
            non_pit=False,
            note=data["note"],
            production_ok=ok,
            gate_reason=None if ok else "quote_empty",
        )

    def batch_get_quotes(self, codes: Optional[List[str]] = None) -> Dict[str, dict]:
        """批量现价；委托 QuotePort（腾讯 batch）。"""
        batch = [str(c).strip() for c in (codes or []) if str(c).strip()]
        if not batch:
            return {}
        try:
            return dict(self.ports.quote.batch_query(batch) or {})
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            return {}

    def get_index_bars(
        self,
        code: str,
        *,
        limit: int = 120,
    ) -> BarsResult:
        """指数日线信封（质量/来源对齐个股 bars）。"""
        raw = str(code or "").strip()
        try:
            bars, label = self.ports.index.fetch_index(raw, limit=int(limit or 120))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            bars, label = [], "empty"
        bars = list(bars or [])
        data_source = str(label or "index")
        quality_raw = assess_quality(bars, data_source=data_source)
        quality = QualityMeta.from_mapping(quality_raw)
        fallback = not bars or data_source in ("empty", "error")
        prod_ok, gate_reason = allows_production_score(
            quality_level=quality.level,
            fallback=fallback,
        )
        if bars:
            _bump_metric("bars_ok")
        else:
            _bump_metric("bars_empty")
        return BarsResult(
            stock_code=raw,
            bars=bars,
            data_source=data_source,
            adjust="none",
            adjust_policy="raw",
            fallback=fallback,
            quality=quality,
            production_ok=prod_ok,
            gate_reason=gate_reason or None,
            pit=PitMeta(
                bars_pit=False,
                note="指数日线快照；非 PIT 切条。",
            ),
            bar_count=len(bars),
            date_min=bars[0].get("date") if bars else None,
            date_max=bars[-1].get("date") if bars else None,
            fetched_at=datetime.now().isoformat(timespec="seconds"),
            non_pit=True,
            ok=bool(bars),
            kind="index_bars",
        )

    def get_minute_bars(
        self,
        code: str,
        *,
        period: str = "5",
        lookback_days: int = 30,
        use_cache: bool = True,
        max_age_hours: float = MINUTE_CACHE_HOURS,
    ) -> DataEnvelope:
        raw = str(code or "").strip()
        try:
            packed = self.ports.minute.fetch_minute(
                raw,
                period=str(period or "5"),
                lookback_days=int(lookback_days or 30),
                use_cache=bool(use_cache),
                max_age_hours=float(max_age_hours or MINUTE_CACHE_HOURS),
            )
            if isinstance(packed, tuple) and len(packed) >= 2:
                bars, meta = packed[0], packed[1]
            else:
                bars, meta = packed, {}
        except TypeError:
            packed = self.ports.minute.fetch_minute(raw)
            if isinstance(packed, tuple) and len(packed) >= 2:
                bars, meta = packed[0], packed[1]
            else:
                bars, meta = packed, {}
        except Exception as e:
            logger.exception('unexpected error in get_minute_bars')
            return DataEnvelope(
                kind="minute",
                code=raw,
                ok=False,
                data={
                    "success": False,
                    "stock_code_query": raw,
                    "bars": [],
                    "error": str(e),
                },
                data_source="minute_error",
                fetched_at=datetime.now().isoformat(timespec="seconds"),
                quality=QualityMeta(level="empty"),
                note="分钟拉取失败；τ>open 的 Z 不可用。",
                production_ok=False,
                gate_reason="minute_error",
            )
        meta = meta if isinstance(meta, dict) else {}
        bar_list = list(bars or []) if not isinstance(bars, dict) else []
        ok = bool(bar_list)
        return DataEnvelope(
            kind="minute",
            code=raw,
            ok=ok,
            data={
                "success": ok,
                "stock_code_query": raw,
                "bars": bar_list,
                "bar_count": len(bar_list),
                "period": str(period or "5"),
                "meta": meta,
                "note": "分钟缓存/拉取；供 ŷ_τ@10:30（≈12×5m）等；非 EOD 主轴。",
            },
            data_source=str(meta.get("data_source") or "minute"),
            fetched_at=datetime.now().isoformat(timespec="seconds"),
            quality=QualityMeta(
                level="snapshot" if bar_list else "empty",
                bar_count=len(bar_list),
            ),
            non_pit=True,
            note="分钟缓存/拉取；供 ŷ_τ@10:30（≈12×5m）等；非 EOD 主轴。",
            production_ok=False,
            gate_reason="minute_non_pit" if ok else "minute_empty",
        )

    def get_bars(
        self,
        code: str,
        *,
        limit: int = 120,
        use_cache: bool = True,
        cache_max_age_hours: float = DAILY_CACHE_HOURS,
        as_of: Optional[str] = None,
        incremental: bool = True,
        adjust: Optional[str] = None,
        offline_ok: bool = False,
        offline_only: bool = False,
        reject_quote_fallback: Optional[bool] = None,
    ) -> BarsResult:
        if reject_quote_fallback is None:
            reject_quote_fallback = bool(offline_ok or offline_only)
        from core.data.pit import bars_as_of

        policy = normalize_adjust_policy(adjust)
        raw = str(code or "").strip()
        bars, data_source = self.ports.bars.fetch_daily(
            raw,
            limit=limit,
            use_cache=use_cache,
            cache_max_age_hours=cache_max_age_hours,
            incremental=incremental,
            adjust=policy,
            offline_ok=offline_ok,
            offline_only=offline_only,
        )
        bars = list(bars or [])
        data_source = str(data_source or "")

        cutoff = str(as_of or "").strip()
        if cutoff:
            bars = bars_as_of(bars, cutoff, inclusive=True)
            pit = PitMeta(
                bars_pit=True,
                as_of=cutoff,
                bar_count=len(bars),
                note="日线已 as_of 切条；基本面仍可能非 PIT。",
            )
        else:
            pit = PitMeta(
                bars_pit=False,
                as_of=None,
                note="未指定 as_of；回测请用 window_as_of。",
            )

        adjust_tag = infer_adjust(data_source, policy=policy)
        if ":raw" in data_source or policy == "raw":
            adjust_tag = "raw"
        elif ":hfq" in data_source or policy == "hfq":
            adjust_tag = "hfq" if "hfq" in data_source or policy == "hfq" else adjust_tag

        quality_raw = assess_quality(bars, data_source=data_source)
        quality = QualityMeta.from_mapping(quality_raw)
        fallback = data_source in ("empty", "quote_fallback") or "fallback" in data_source
        if reject_quote_fallback and (
            fallback or quality.pseudo_dates or "quote_fallback" in data_source
        ):
            bars = []
            data_source = "rejected_quote_fallback"
            fallback = True
            quality = QualityMeta.from_mapping(
                assess_quality(bars, data_source=data_source)
            )
            pit = PitMeta(
                bars_pit=pit.bars_pit,
                as_of=pit.as_of,
                bar_count=0,
                note="已拒绝 quote_fallback / 伪日线（研究/回测门禁）。",
                rejected_quote_fallback=True,
                extras=dict(pit.extras),
            )

        prod_ok, gate_reason = allows_production_score(
            quality_level=quality.level,
            fallback=fallback,
        )
        if data_source == "rejected_quote_fallback":
            _bump_metric("bars_rejected_fallback")
        elif offline_only and not bars:
            _bump_metric("bars_offline_miss")
        elif fallback or not bars:
            if fallback:
                _bump_metric("bars_fallback")
            if not bars:
                _bump_metric("bars_empty")
        else:
            _bump_metric("bars_ok")
        return BarsResult(
            stock_code=raw,
            bars=bars,
            data_source=data_source,
            adjust=adjust_tag,
            adjust_policy=policy,
            fallback=fallback,
            quality=quality,
            production_ok=prod_ok,
            gate_reason=gate_reason or None,
            pit=pit,
            bar_count=len(bars),
            date_min=bars[0].get("date") if bars else None,
            date_max=bars[-1].get("date") if bars else None,
            fetched_at=datetime.now().isoformat(timespec="seconds"),
            non_pit=False,
            ok=bool(bars) and not fallback,
        )

    def bars_and_source(
        self, code: str, *, limit: int = 120, **kwargs: Any
    ) -> Tuple[List[dict], str]:
        pack = self.get_bars(code, limit=limit, **kwargs)
        return list(pack.bars or []), str(pack.data_source or "empty")

    def index_bars_and_source(
        self, code: str, *, limit: int = 120
    ) -> Tuple[List[dict], str]:
        pack = self.get_index_bars(code, limit=limit)
        return list(pack.bars or []), str(pack.data_source or "index")

    @staticmethod
    def _empty_bars_pack(code: str, *, gate_reason: str = "pool_worker_failed") -> Dict[str, Any]:
        return {
            "stock_code": code,
            "bars": [],
            "data_source": "empty",
            "fallback": True,
            "production_ok": False,
            "gate_reason": gate_reason,
            "bar_count": 0,
            "quality": {"level": "empty"},
            "ok": False,
        }

    def _bars_batch_local(
        self,
        codes: List[str],
        *,
        limit: int = 120,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """线程内 self.get_bars：尊重实例 ports（测试注入 / 非默认 Service）。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if not codes:
            return []
        if len(codes) == 1:
            try:
                return [self.get_bars(codes[0], limit=limit, **kwargs).as_dict()]
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                _bump_metric("pool_worker_failed")
                return [self._empty_bars_pack(codes[0])]

        out_map: Dict[str, Dict[str, Any]] = {}
        workers = min(8, len(codes))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(self.get_bars, c, limit=limit, **kwargs): c for c in codes
            }
            for fut in as_completed(futs):
                code = futs[fut]
                try:
                    out_map[code] = fut.result().as_dict()
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                    _bump_metric("pool_worker_failed")
                    out_map[code] = self._empty_bars_pack(code)
        return [out_map.get(c) or self._empty_bars_pack(c) for c in codes]

    def get_bars_batch(
        self,
        codes: Optional[List[str]] = None,
        *,
        limit: int = 120,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """批量日线；默认 Service 走进程池 worker，其它实例走线程以保留注入 ports。"""
        kwargs.setdefault("adjust", DEFAULT_ADJUST_POLICY)
        watch = [str(c).strip() for c in (codes or []) if str(c).strip()]
        if not watch:
            return []
        # 进程池 worker 只能 pickle 顶层函数 → 全局 singleton；非默认实例必须本地路径。
        if self is not get_default_service():
            return self._bars_batch_local(watch, limit=limit, **kwargs)

        from core.ports.market import batch_map

        packs = batch_map(bars_pack_worker, watch, limit=limit, **kwargs)
        out: List[Dict[str, Any]] = []
        for code, pack in zip(watch, packs):
            if isinstance(pack, dict):
                out.append(pack)
            else:
                _bump_metric("pool_worker_failed")
                out.append(self._empty_bars_pack(code))
        return out

    def metrics_snapshot(self) -> Dict[str, int]:
        return metrics_snapshot()

    def get_fundamentals(
        self,
        code: str,
        *,
        use_cache: bool = True,
        cache_max_age_hours: float = FUNDAMENTALS_CACHE_HOURS,
        as_of: Optional[str] = None,
        live: bool = True,
        **kwargs: Any,
    ) -> DataEnvelope:
        raw = str(code or "").strip()
        cutoff = str(as_of or "").strip()
        fetched = datetime.now().isoformat(timespec="seconds")

        if cutoff:
            from core.fundamentals_pit import resolve_fundamentals_for_score
            from core.signal.config import load_signal_config

            fund_cfg = load_signal_config().get("fundamentals") or {}
            resolved = resolve_fundamentals_for_score(
                raw, as_of=cutoff, fund_cfg=fund_cfg, live_fallback=False
            )
            metrics = resolved.get("metrics")
            ok = bool(metrics)
            data = {
                "success": ok,
                "stock_code": raw,
                "metrics": metrics or {},
                "as_of": resolved.get("as_of"),
                "decision_as_of": cutoff,
                "cache_hit": True,
                "fundamentals_pit": bool(
                    resolved.get("fundamentals_pit") and resolved.get("ok")
                ),
                "pit_meta": resolved.get("pit_meta") or {},
                "mode": resolved.get("mode"),
                "ann_missing": bool(resolved.get("ann_missing")),
                "note": resolved.get("note")
                or "财务 as_of 选取；缺失不回退未来快照。",
            }
            return DataEnvelope(
                kind="fundamentals",
                code=raw,
                ok=ok,
                data=data,
                data_source="fundamentals_history_pit",
                fetched_at=fetched,
                quality=QualityMeta(level="pit" if metrics else "empty"),
                non_pit=bool(resolved.get("non_pit")),
                note=str(data["note"]),
                pit={
                    "fundamentals_pit": data["fundamentals_pit"],
                    "as_of": data.get("as_of"),
                    "decision_as_of": cutoff,
                },
                production_ok=bool(
                    data["fundamentals_pit"] and ok and not data["ann_missing"]
                ),
                gate_reason=(
                    None
                    if (data["fundamentals_pit"] and ok and not data["ann_missing"])
                    else "fundamentals_pit_incomplete"
                ),
            )

        if use_cache:
            cached = self.ports.snapshots.load(
                "fundamentals", raw, max_age_hours=cache_max_age_hours
            )
            if cached:
                payload, meta = cached
                base = payload if isinstance(payload, dict) else {"metrics": payload}
                data = {
                    **base,
                    "stock_code": raw,
                    "cache_hit": True,
                    "fundamentals_pit": False,
                    "note": "基本面快照缓存；非公告日 PIT。传 as_of= 可走 history。",
                }
                return DataEnvelope(
                    kind="fundamentals",
                    code=raw,
                    ok=True,
                    data=data,
                    data_source=f"cache:{(meta or {}).get('data_source') or 'fundamentals'}",
                    fetched_at=str((meta or {}).get("fetched_at") or fetched),
                    quality=QualityMeta(level="snapshot"),
                    non_pit=True,
                    note=data["note"],
                    production_ok=False,
                    gate_reason="fundamentals_snapshot_non_pit",
                )

        if not live:
            return DataEnvelope(
                kind="fundamentals",
                code=raw,
                ok=False,
                data={
                    "success": False,
                    "stock_code": raw,
                    "error": "cache_miss_no_live",
                    "cache_hit": False,
                    "fundamentals_pit": False,
                    "note": "live=False 且无可用快照缓存",
                },
                data_source="empty",
                fetched_at=fetched,
                quality=QualityMeta(level="empty"),
                non_pit=True,
                note="live=False 且无可用快照缓存",
                production_ok=False,
                gate_reason="fundamentals_cache_miss",
            )

        try:
            payload_live = self.ports.fundamentals.build(raw, **kwargs)
        except Exception as e:
            logger.exception('unexpected error in get_fundamentals')
            return DataEnvelope(
                kind="fundamentals",
                code=raw,
                ok=False,
                data={
                    "success": False,
                    "stock_code": raw,
                    "error": str(e),
                    "cache_hit": False,
                    "fundamentals_pit": False,
                },
                data_source="empty",
                fetched_at=fetched,
                quality=QualityMeta(level="empty"),
                non_pit=True,
                production_ok=False,
                gate_reason="fundamentals_error",
            )

        if not isinstance(payload_live, dict):
            payload_live = {"success": False, "raw": payload_live}
        src = "akshare_fundamentals"
        if payload_live.get("sources"):
            src = ",".join(str(s) for s in (payload_live.get("sources") or [])[:3]) or src
        if use_cache and payload_live.get("success"):
            try:
                self.ports.snapshots.save(
                    "fundamentals", raw, payload_live, data_source=src
                )
            except OSError:
                pass
        ok = bool(payload_live.get("success"))
        data = {
            **payload_live,
            "stock_code": raw,
            "cache_hit": False,
            "fundamentals_pit": False,
            "note": "基本面为当前快照；非 PIT。传 as_of= 可走 history。",
        }
        return DataEnvelope(
            kind="fundamentals",
            code=raw,
            ok=ok,
            data=data,
            data_source=src,
            fetched_at=fetched,
            quality=QualityMeta(level="snapshot" if ok else "empty"),
            non_pit=True,
            note=data["note"],
            production_ok=False,
            gate_reason="fundamentals_snapshot_non_pit",
        )

    def get_news(
        self,
        code: str,
        *,
        limit: int = 8,
        use_cache: bool = True,
        cache_max_age_hours: float = NEWS_CACHE_HOURS,
        **kwargs: Any,
    ) -> DataEnvelope:
        raw = str(code or "").strip()
        fetched = datetime.now().isoformat(timespec="seconds")
        if use_cache:
            cached = self.ports.snapshots.load(
                _NEWS_SNAPSHOT_KIND, raw, max_age_hours=cache_max_age_hours
            )
            payload = cached[0] if cached else None
            if isinstance(payload, dict):
                items = list(payload.get("items") or [])
            elif isinstance(payload, list):
                items = list(payload)
            else:
                items = []
            if cached and items:
                meta = cached[1]
                base = payload if isinstance(payload, dict) else {"items": payload}
                data = {
                    **base,
                    "stock_code": raw,
                    "cache_hit": True,
                    "success": True,
                    "note": "资讯标题缓存；非历史 PIT 面板。",
                }
                return DataEnvelope(
                    kind="news",
                    code=raw,
                    ok=True,
                    data=data,
                    data_source=f"cache:{(meta or {}).get('data_source') or 'news'}",
                    fetched_at=str((meta or {}).get("fetched_at") or fetched),
                    quality=QualityMeta(level="snapshot"),
                    non_pit=True,
                    note=data["note"],
                    production_ok=False,
                    gate_reason="news_non_pit",
                )
        try:
            live = self.ports.news.build(raw, limit=limit, **kwargs)
        except Exception as e:
            logger.exception('unexpected error in get_news')
            return DataEnvelope(
                kind="news",
                code=raw,
                ok=False,
                data={
                    "success": False,
                    "stock_code": raw,
                    "error": str(e),
                    "items": [],
                    "cache_hit": False,
                },
                data_source="empty",
                fetched_at=fetched,
                quality=QualityMeta(level="empty"),
                non_pit=True,
                production_ok=False,
                gate_reason="news_error",
            )
        if not isinstance(live, dict):
            live = {"success": False, "items": []}
        src = "akshare_news"
        if use_cache and (live.get("success") or live.get("items")):
            try:
                self.ports.snapshots.save(
                    _NEWS_SNAPSHOT_KIND, raw, live, data_source=src
                )
            except OSError:
                pass
        ok = bool(live.get("items") or live.get("success"))
        data = {
            **live,
            "stock_code": raw,
            "cache_hit": False,
            "success": ok,
            "note": "资讯为实时拉取快照；非 PIT。",
        }
        return DataEnvelope(
            kind="news",
            code=raw,
            ok=ok,
            data=data,
            data_source=src,
            fetched_at=fetched,
            quality=QualityMeta(level="snapshot" if ok else "empty"),
            non_pit=True,
            note=data["note"],
            production_ok=False,
            gate_reason="news_non_pit",
        )

    def get_spot(self, *, force: bool = False, disk_only: bool = False) -> DataEnvelope:
        fetched = datetime.now().isoformat(timespec="seconds")
        if disk_only:
            from core.data.policy import SPOT_DISK_MAX_AGE_HOURS

            try:
                rows = self.ports.spot.load_disk(max_age_hours=SPOT_DISK_MAX_AGE_HOURS)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                rows = None
            rows = list(rows or [])
            has_rows = bool(rows)
            data = {
                "ok": has_rows,
                "rows": rows,
                "count": len(rows),
                "note": "A 股现货磁盘缓存；非 PIT。",
            }
            return DataEnvelope(
                kind="spot",
                code="",
                ok=has_rows,
                data=data,
                data_source="disk" if has_rows else "empty",
                fetched_at=fetched,
                quality=QualityMeta(level="snapshot" if has_rows else "empty"),
                non_pit=True,
                note=data["note"],
                production_ok=False,
                gate_reason="spot_disk" if has_rows else "spot_empty",
            )

        rows = self.ports.spot.fetch(force=bool(force))
        src = "akshare_spot"
        try:
            last = getattr(self.ports.spot, "last_source", None)
            if last:
                src = str(last)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            pass
        has_rows = bool(rows)
        data = {
            "ok": has_rows,
            "rows": rows or [],
            "count": len(rows or []),
            "note": "A 股现货快照；供估值列表；非 PIT。",
        }
        return DataEnvelope(
            kind="spot",
            code="",
            ok=has_rows,
            data=data,
            data_source=src,
            fetched_at=fetched,
            quality=QualityMeta(level="snapshot" if has_rows else "empty"),
            non_pit=True,
            note=data["note"],
            production_ok=False,
            gate_reason="spot_non_pit" if has_rows else "spot_empty",
        )

    def get_macro_snapshot(self, *, max_age_hours: float = 36.0) -> DataEnvelope:
        from core.market.context_store import load_macro_snapshot

        fetched = datetime.now().isoformat(timespec="seconds")
        snap, meta = load_macro_snapshot(max_age_hours=max_age_hours)
        ok = isinstance(snap, dict) and bool(snap.get("series"))
        data = {
            **(snap or {}),
            "cache_hit": bool((meta or {}).get("cache_hit")),
            "success": ok,
            "note": "跨市场宏观快照；非 PIT。",
        }
        return DataEnvelope(
            kind="macro",
            code="latest",
            ok=ok,
            data=data,
            data_source=str((meta or {}).get("data_source") or "macro_snapshot"),
            fetched_at=str((meta or {}).get("fetched_at") or fetched),
            quality=QualityMeta(level="snapshot" if ok else "empty"),
            non_pit=True,
            note=data["note"],
            production_ok=False,
            gate_reason="macro_non_pit",
        )

    def get_market_sentiment_snapshot(self, *, max_age_hours: float = 36.0) -> DataEnvelope:
        from core.market.context_store import load_market_sentiment_snapshot

        fetched = datetime.now().isoformat(timespec="seconds")
        snap, meta = load_market_sentiment_snapshot(max_age_hours=max_age_hours)
        ok = isinstance(snap, dict) and snap.get("success")
        data = {
            **(snap or {}),
            "cache_hit": bool((meta or {}).get("cache_hit")),
            "success": bool(ok),
            "note": "市场情绪快照；非 PIT。",
        }
        return DataEnvelope(
            kind="market_sentiment",
            code="latest",
            ok=bool(ok),
            data=data,
            data_source=str((meta or {}).get("data_source") or "market_sentiment"),
            fetched_at=str((meta or {}).get("fetched_at") or fetched),
            quality=QualityMeta(level="snapshot" if ok else "empty"),
            non_pit=True,
            note=data["note"],
            production_ok=False,
            gate_reason="market_sentiment_non_pit",
        )

    def get_announcement_snapshot(self, *, max_age_hours: float = 36.0) -> DataEnvelope:
        from core.market.context_store import load_announcement_snapshot

        fetched = datetime.now().isoformat(timespec="seconds")
        snap, meta = load_announcement_snapshot(max_age_hours=max_age_hours)
        ok = isinstance(snap, dict) and snap.get("success")
        data = {
            **(snap or {}),
            "cache_hit": bool((meta or {}).get("cache_hit")),
            "success": bool(ok),
            "note": "公告/IPO 扫描快照；非 PIT。",
        }
        return DataEnvelope(
            kind="announcement",
            code="latest",
            ok=bool(ok),
            data=data,
            data_source=str((meta or {}).get("data_source") or "announcement"),
            fetched_at=str((meta or {}).get("fetched_at") or fetched),
            quality=QualityMeta(level="snapshot" if ok else "empty"),
            non_pit=True,
            note=data["note"],
            production_ok=False,
            gate_reason="announcement_non_pit",
        )

    def summarize_data_quality(
        self,
        codes: Optional[List[str]] = None,
        *,
        limit: int = 60,
    ) -> Dict[str, Any]:
        _raw = [str(c).strip() for c in (codes or []) if str(c).strip()]
        _seen: set = set()
        watch = [c for c in _raw if not (c in _seen or _seen.add(c))]
        items: List[Dict[str, Any]] = []
        levels = {"good": 0, "thin": 0, "empty": 0}
        fallback_n = 0
        packs = self.get_bars_batch(watch, limit=limit)
        for code, pack in zip(watch, packs):
            if not isinstance(pack, dict):
                items.append(
                    {
                        "stock_code": code,
                        "ok": False,
                        "error": "pool_worker_failed",
                        "quality": {"level": "empty"},
                        "fallback": True,
                        "production_ok": False,
                        "gate_reason": "data_quality_gate:empty",
                        "adjust_policy": DEFAULT_ADJUST_POLICY,
                    }
                )
                levels["empty"] += 1
                fallback_n += 1
                continue
            q = pack.get("quality") or {}
            level = str(q.get("level") or "empty")
            levels[level] = levels.get(level, 0) + 1
            if pack.get("fallback"):
                fallback_n += 1
            item_ok = bool(pack.get("production_ok"))
            items.append(
                {
                    "stock_code": code,
                    "ok": item_ok,
                    "data_source": pack.get("data_source"),
                    "adjust": pack.get("adjust"),
                    "adjust_policy": pack.get("adjust_policy") or DEFAULT_ADJUST_POLICY,
                    "fallback": bool(pack.get("fallback")),
                    "quality": q,
                    "production_ok": item_ok,
                    "gate_reason": pack.get("gate_reason"),
                    "date_min": pack.get("date_min"),
                    "date_max": pack.get("date_max"),
                    "bar_count": pack.get("bar_count"),
                }
            )
        gated = sum(1 for it in items if not it.get("production_ok"))
        return {
            "ok": True,
            "count": len(items),
            "levels": levels,
            "fallback_count": fallback_n,
            "gated_count": gated,
            "adjust_policy": DEFAULT_ADJUST_POLICY,
            "metrics": self.metrics_snapshot(),
            "items": items,
            "note": (
                "N1/M1 DataService 质量摘要；P1 门禁：thin/empty/fallback 不计 "
                "production_ok；不代客下单。"
            ),
        }


class ResearchDataService(MarketDataService):
    """研究/回测默认：拒绝 quote_fallback。"""

    def get_bars(self, code: str, **kwargs: Any) -> BarsResult:
        kwargs.setdefault("reject_quote_fallback", True)
        return super().get_bars(code, **kwargs)

    def bars_and_source(
        self, code: str, *, limit: int = 120, **kwargs: Any
    ) -> Tuple[List[dict], str]:
        kwargs.setdefault("reject_quote_fallback", True)
        return super().bars_and_source(code, limit=limit, **kwargs)

    def get_bars_batch(
        self,
        codes: Optional[List[str]] = None,
        *,
        limit: int = 120,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        kwargs.setdefault("reject_quote_fallback", True)
        kwargs.setdefault("adjust", DEFAULT_ADJUST_POLICY)
        watch = [str(c).strip() for c in (codes or []) if str(c).strip()]
        if not watch:
            return []
        if self is not get_research_service():
            return self._bars_batch_local(watch, limit=limit, **kwargs)

        from core.ports.market import batch_map

        packs = batch_map(research_bars_pack_worker, watch, limit=limit, **kwargs)
        out: List[Dict[str, Any]] = []
        for code, pack in zip(watch, packs):
            if isinstance(pack, dict):
                out.append(pack)
            else:
                _bump_metric("pool_worker_failed")
                out.append(self._empty_bars_pack(code))
        return out


_DEFAULT: Optional[MarketDataService] = None
_RESEARCH: Optional[ResearchDataService] = None


def get_default_service() -> MarketDataService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = MarketDataService()
    return _DEFAULT


def get_research_service() -> ResearchDataService:
    global _RESEARCH
    if _RESEARCH is None:
        _RESEARCH = ResearchDataService()
    return _RESEARCH


def set_default_service(svc: Optional[MarketDataService]) -> None:
    """测试注入生产默认 Service；svc=None 时一并清空 research。"""
    global _DEFAULT, _RESEARCH
    _DEFAULT = svc
    if svc is None:
        _RESEARCH = None


def set_research_service(svc: Optional[ResearchDataService]) -> None:
    """测试注入研究 Service。"""
    global _RESEARCH
    _RESEARCH = svc
