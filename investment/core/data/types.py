"""DataService 返回信封（DS encapsulate A）。

BarsResult / DataEnvelope 提供属性访问；as_dict() 与历史 dict 契约对齐。
"""


import logging

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass
class QualityMeta:
    level: str = "empty"
    bar_count: int = 0
    first_date: Any = None
    last_date: Any = None
    notes: List[str] = field(default_factory=list)
    pseudo_dates: bool = False

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, Any]]) -> "QualityMeta":
        m = dict(raw or {})
        return cls(
            level=str(m.get("level") or "empty"),
            bar_count=int(m.get("bar_count") or 0),
            first_date=m.get("first_date"),
            last_date=m.get("last_date"),
            notes=list(m.get("notes") or []),
            pseudo_dates=bool(m.get("pseudo_dates")),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "bar_count": self.bar_count,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "notes": list(self.notes),
            "pseudo_dates": self.pseudo_dates,
        }


@dataclass
class PitMeta:
    bars_pit: bool = False
    as_of: Optional[str] = None
    bar_count: Optional[int] = None
    note: str = ""
    rejected_quote_fallback: bool = False
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "bars_pit": self.bars_pit,
            "as_of": self.as_of,
            "note": self.note,
        }
        if self.bar_count is not None:
            out["bar_count"] = self.bar_count
        if self.rejected_quote_fallback:
            out["rejected_quote_fallback"] = True
        out.update(self.extras)
        return out


@dataclass
class BarsResult:
    """日线读结果信封。"""

    stock_code: str
    bars: List[dict]
    data_source: str
    adjust: str
    adjust_policy: str
    fallback: bool
    quality: QualityMeta
    production_ok: bool
    gate_reason: Optional[str]
    pit: PitMeta
    bar_count: int
    date_min: Any = None
    date_max: Any = None
    fetched_at: str = ""
    non_pit: bool = False
    kind: str = "bars"
    ok: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "ok": self.ok,
            "stock_code": self.stock_code,
            "bars": list(self.bars or []),
            "data_source": self.data_source,
            "adjust": self.adjust,
            "adjust_policy": self.adjust_policy,
            "fallback": self.fallback,
            "quality": self.quality.as_dict() if isinstance(self.quality, QualityMeta) else dict(self.quality or {}),
            "production_ok": self.production_ok,
            "gate_reason": self.gate_reason,
            "pit": self.pit.as_dict() if isinstance(self.pit, PitMeta) else dict(self.pit or {}),
            "bar_count": self.bar_count,
            "date_min": self.date_min,
            "date_max": self.date_max,
            "fetched_at": self.fetched_at,
            "non_pit": self.non_pit,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)


@dataclass
class DataEnvelope:
    """通用读结果（quote / minute / fundamentals / news / spot）。"""

    kind: str
    code: str
    ok: bool
    data: Dict[str, Any]
    data_source: str
    fetched_at: str
    quality: QualityMeta = field(default_factory=QualityMeta)
    non_pit: bool = True
    note: str = ""
    production_ok: Optional[bool] = None
    gate_reason: Optional[str] = None
    pit: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        """展平为历史兼容 dict（payload 字段并入顶层）。"""
        out = dict(self.data or {})
        out.setdefault("kind", self.kind)
        out.setdefault("ok", self.ok)
        out.setdefault("stock_code_query", self.code)
        out.setdefault("data_source", self.data_source)
        out.setdefault("fetched_at", self.fetched_at)
        out.setdefault("non_pit", self.non_pit)
        if self.note and "note" not in out:
            out["note"] = self.note
        if "quality" not in out:
            out["quality"] = self.quality.as_dict()
        if self.production_ok is not None:
            out["production_ok"] = self.production_ok
        if self.gate_reason is not None:
            out["gate_reason"] = self.gate_reason
        if self.pit is not None:
            out.setdefault("pit", self.pit)
        return out

    def get(self, key: str, default: Any = None) -> Any:
        return self.as_dict().get(key, default)
