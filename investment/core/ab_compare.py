"""S2.2 · A/B 对照指纹：两套配置/结果并排，供验证包与研究导出。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional


def _fp(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _slim_metrics(result: Optional[dict]) -> Dict[str, Any]:
    r = result or {}
    m = r.get("metrics") if isinstance(r.get("metrics"), dict) else r
    keys = (
        "total_return_pct",
        "max_drawdown_pct",
        "win_rate_pct",
        "sharpe",
        "trade_count",
        "ic_mean",
        "icir",
    )
    out = {k: m.get(k) for k in keys if isinstance(m, dict) and k in m}
    if r.get("params"):
        out["params"] = {
            k: (r.get("params") or {}).get(k)
            for k in ("top_k", "lookback", "horizon_days", "weight_mode", "neutralize")
            if k in (r.get("params") or {})
        }
    return out


def build_ab_compare(
    *,
    label_a: str = "A",
    label_b: str = "B",
    result_a: Optional[dict] = None,
    result_b: Optional[dict] = None,
    config_a: Optional[dict] = None,
    config_b: Optional[dict] = None,
    note: str = "",
) -> Dict[str, Any]:
    slim_a = _slim_metrics(result_a)
    slim_b = _slim_metrics(result_b)
    fp_a = _fp({"label": label_a, "metrics": slim_a, "weights": (config_a or {}).get("weights")})
    fp_b = _fp({"label": label_b, "metrics": slim_b, "weights": (config_b or {}).get("weights")})
    delta: Dict[str, Any] = {}
    for k in set(slim_a) | set(slim_b):
        if k == "params":
            continue
        va, vb = slim_a.get(k), slim_b.get(k)
        try:
            if va is not None and vb is not None:
                delta[k] = round(float(vb) - float(va), 4)
        except (TypeError, ValueError):
            continue
    return {
        "ok": True,
        "kind": "ab_compare",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "note": note
        or "A/B 对照指纹：并排 metrics；不相同则 fingerprint 不同。非完整配对回测引擎。",
        "a": {"label": label_a, "fingerprint": fp_a, "metrics": slim_a},
        "b": {"label": label_b, "fingerprint": fp_b, "metrics": slim_b},
        "delta_b_minus_a": delta,
        "same_fingerprint": fp_a == fp_b,
    }
