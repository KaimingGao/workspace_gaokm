"""X 轨 · live 特征同构：财务 PIT / 指数 / 市场深度（与 research panel 同源 resolve）。"""


import logging

logger = logging.getLogger(__name__)
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_INDEX_CACHE: Dict[str, Tuple[float, List[dict], str]] = {}
_INDEX_LOCK = threading.Lock()
_INDEX_TTL_SEC = 300.0


def live_decision_as_of(*, bars: Optional[List[dict]] = None) -> str:
    """Live 决策日：优先末日线日期，否则会话交易日。"""
    for b in reversed(bars or []):
        d = str((b or {}).get("date") or "").strip()[:10]
        if len(d) >= 10 and d[4] == "-" and d[7] == "-":
            return d
    try:
        from core.market_calendar import resolve_session_date

        return resolve_session_date()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in live_features.py", exc_info=True)
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d")


def resolve_live_fundamentals(
    stock_code: str,
    *,
    as_of: Optional[str] = None,
    bars: Optional[List[dict]] = None,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    """Live 财务：pit_mode=as_of 时与 panel/OLS 同源 resolve；返回 metrics + meta。"""
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in live_features.py", exc_info=True)
            config = {}
    fund_cfg = dict((config or {}).get("fundamentals") or {})
    pit_mode = str(fund_cfg.get("pit_mode") or "as_of").strip().lower()
    decision = as_of or live_decision_as_of(bars=bars)

    from core.fundamentals_pit import resolve_fundamentals_for_score

    if pit_mode in ("snapshot", "off", "none"):
        resolved = resolve_fundamentals_for_score(
            stock_code,
            as_of=None,
            fund_cfg=fund_cfg,
            live_fallback=True,
        )
    else:
        resolved = resolve_fundamentals_for_score(
            stock_code,
            as_of=decision,
            fund_cfg=fund_cfg,
            live_fallback=False,
        )

    metrics = resolved.get("metrics") if isinstance(resolved, dict) else None
    pit_meta = dict((resolved or {}).get("pit_meta") or {})
    ann_missing = bool(
        (resolved or {}).get("ann_missing")
        or pit_meta.get("ann_missing")
        or pit_meta.get("selected_ann_missing")
    )

    meta = {
        "as_of": resolved.get("as_of") or resolved.get("decision_as_of") or decision,
        "decision_as_of": decision,
        "mode": resolved.get("mode"),
        "fundamentals_pit": bool(resolved.get("fundamentals_pit")),
        "non_pit": bool(resolved.get("non_pit")),
        "ok": bool(resolved.get("ok")),
        "history_count": resolved.get("history_count"),
        "ann_missing": ann_missing,
        "policy": resolved.get("policy") or fund_cfg.get("missing_as_of_policy"),
        "note": resolved.get("note"),
        "source": "resolve_fundamentals_for_score",
    }
    return {
        "metrics": metrics if isinstance(metrics, dict) else None,
        "fundamentals_pit": meta,
        "resolved": resolved,
    }


def fetch_live_index_bars(
    *,
    market: str = "CN",
    limit: int = 75,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Live 指数日线（短缓存）；失败时 bars=[] 并写 reason。经 DataService。"""
    from core.data.service import get_default_service
    from core.ports.market import default_benchmark

    bench = str(default_benchmark(market) or "").strip() or "sh000300"
    now = time.time()
    if use_cache:
        with _INDEX_LOCK:
            hit = _INDEX_CACHE.get(bench)
            if hit and (now - hit[0]) < _INDEX_TTL_SEC and hit[1]:
                return {
                    "ok": True,
                    "benchmark": bench,
                    "bars": hit[1],
                    "label": hit[2],
                    "cached": True,
                    "reason": None,
                }

    bars: List[dict] = []
    label = "empty"
    reason = None
    try:
        pack = get_default_service().get_index_bars(bench, limit=int(limit))
        bars = list(pack.bars or [])
        label = str(pack.data_source or "index")
        if not bars:
            reason = "no_index"
    except Exception as exc:
        logger.exception('unexpected error in fetch_live_index_bars')
        bars = []
        label = "error"
        reason = f"index_fetch_failed:{exc}"

    if bars and use_cache:
        with _INDEX_LOCK:
            _INDEX_CACHE[bench] = (now, bars, str(label or ""))

    return {
        "ok": bool(bars),
        "benchmark": bench,
        "bars": bars,
        "label": label,
        "cached": False,
        "reason": reason,
    }


def infer_fundamentals_depth(stock_code: str, *, quote: Optional[dict] = None) -> Dict[str, Any]:
    """X5：财务深度边界（CN full / HK·US shallow）。"""
    code = str(stock_code or "").strip().upper()
    market = "CN"
    try:
        from core.ports.market import resolve_market_code

        market = str(resolve_market_code(code) or "CN").upper()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in live_features.py", exc_info=True)
        if code.startswith(("0", "3", "6")) and len(code) == 6:
            market = "CN"
        elif code.endswith(".HK") or code.startswith("0") and len(code) == 5:
            market = "HK"
        else:
            q = quote or {}
            m = str(q.get("market") or "").upper()
            if m in ("HK", "US", "CN"):
                market = m

    if market == "CN":
        depth = "cn_full"
        note = "A 股可多期财务序列（需 ingest）；可进估值/质量/成长因子"
    elif market == "HK":
        depth = "hk_shallow"
        note = "港股财务浅（PE/PB/市值级）；深度 ROE/增速未接入 · 不假装有全因子"
    else:
        depth = "us_shallow"
        note = "美股/其他字段有限 · 深度财务因子不可用"

    return {
        "market": market,
        "fundamentals_depth": depth,
        "note": note,
        "sentiment_in_yhat": False,
        "sentiment_note": "舆情无历史面板 → 不可 OLS β；仅 prior 旁路",
    }


def build_quality_policy_snapshot(*, config: Optional[dict] = None) -> Dict[str, Any]:
    """X4：live / 回测 / 研究质量门对照（挂 fit-gap）。"""
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in live_features.py", exc_info=True)
            config = {}
    fund = (config or {}).get("fundamentals") or {}
    regime = (config or {}).get("regime") or {}
    return {
        "live": {
            "quality_gate": True,
            "thin_empty_fallback_reject": True,
            "fundamentals_pit_mode": fund.get("pit_mode") or "as_of",
            "index_bars": True,
            "regime_enabled_factors": True,
        },
        "backtest": {
            "quality_gate": False,
            "note": "engine 直接 score_bars；可模拟 quote_fallback",
        },
        "research_panel": {
            "bypass_score_bars_regime": True,
            "respect_regime_optional": True,
            "note": "默认全注册因子；OOS 应对齐时传 respect_regime=True",
        },
        "regime_cfg_present": bool(regime),
        "track": "X4",
    }
