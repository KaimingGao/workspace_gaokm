"""BacktestService 返回信封（A3）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class BacktestResult:
    """组合/TopK 回测信封；``as_dict()`` 保持历史 dict 契约。"""

    success: bool
    ok: bool
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    bars_backend: Optional[str] = None

    @classmethod
    def from_topk(cls, raw: Any) -> "BacktestResult":
        data = raw if isinstance(raw, dict) else {}
        success = bool(data.get("success", data.get("ok", False)))
        if "success" not in data and "ok" not in data and data.get("equity_curve"):
            success = True
        err = data.get("error")
        if err and not success:
            success = False
        backend = None
        try:
            from core.store import bars_backend

            backend = bars_backend()
        except Exception:  # noqa: BLE001
            backend = None
        out = dict(data)
        if backend and "bars_backend" not in out:
            out["bars_backend"] = backend
        return cls(
            success=success,
            ok=success,
            raw=out,
            error=str(err) if err else None,
            bars_backend=backend,
        )

    def as_dict(self) -> Dict[str, Any]:
        out = dict(self.raw)
        out["success"] = self.success
        out["ok"] = self.ok
        if self.error and "error" not in out:
            out["error"] = self.error
        if self.bars_backend:
            out.setdefault("bars_backend", self.bars_backend)
        return out
