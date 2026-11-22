"""Live 执行方案 = 历史回测页已保存的股票池 + 工厂执行套。

调仓门槛仍读同一份 ``rank_lots``。四头不读套槽 LightGBM：
历史回测选「执行」时用的 ``enabled.live`` 权重，就是 live 打分的权重。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_ARM_SOURCES = {"arm_l", "arm_r", "transformer", "arm_l_open", "arm_r_open", "arm_t_open"}
_SCORE_CACHE: Dict[str, Any] = {"key": None, "table": None}


def live_feature_set(paper: Optional[dict] = None) -> str:
    """历史回测页保存的股票池。缺省沪深300。"""
    from core.paper.rebalance.path_matrix import get_path_matrix_cfg
    from core.universe.feature_sets import FEATURE_HS300, normalize_feature_set

    cfg = get_path_matrix_cfg(paper=paper if isinstance(paper, dict) else None)
    return normalize_feature_set(cfg.get("feature_set")) or FEATURE_HS300


def live_execution_codes(
    paper: Optional[dict] = None,
    *,
    include_held: Sequence[str] = (),
) -> Tuple[List[str], Dict[str, Any]]:
    """调仓宇宙：已保存股票池，再补上已持仓但不在池里的代码（便于清仓）。"""
    from core.universe.feature_sets import FEATURE_POOL, codes_for_feature_set

    fs = live_feature_set(paper)
    try:
        codes = [str(c).strip() for c in codes_for_feature_set(fs, fetch=False) if str(c).strip()]
    except Exception:  # noqa: BLE001
        logger.warning("live execution universe failed", exc_info=True)
        codes = []
    meta: Dict[str, Any] = {
        "source": fs,
        "feature_set": fs,
        "n_watch": len(codes),
        "n_held_extra": 0,
        "name_by_code": {},
    }
    if not codes:
        meta["error"] = "股票池为空" if fs == FEATURE_POOL else "沪深300成分未就绪"
        return [], meta
    seen = set(codes)
    extra = 0
    for raw in include_held or []:
        code = str(raw or "").strip()
        if code and code not in seen:
            codes.append(code)
            seen.add(code)
            extra += 1
    meta["n_held_extra"] = extra
    meta["n_total"] = len(codes)
    return codes, meta


def factory_heads_stamped(rows: Sequence[dict]) -> bool:
    """这批行的四头已经换成工厂执行套，不要再套槽模型盖回去。"""
    for it in rows or []:
        if not isinstance(it, dict):
            continue
        for key in ("y_oo_source", "y_oo_rank_source", "y_tc_rank_source", "_score_source"):
            if str(it.get(key) or "") in _ARM_SOURCES:
                return True
    return False


def stamp_live_execution_heads(
    items: Sequence[dict],
    *,
    day: str,
    paper: Optional[dict] = None,
) -> bool:
    """用工厂 ``enabled.live`` 覆盖当日四头。没有启用版本则不动已有分。

    成功时与历史回测 ``stamp_replay_arm_day`` 同一写入口。
    """
    day_s = str(day or "")[:10]
    rows = [it for it in items or [] if isinstance(it, dict)]
    if len(day_s) < 10 or len(rows) < 2:
        return False
    from core.research.holdout import MODEL_ROLE_LIVE, scoring_model_role_context
    from core.universe.feature_sets import feature_set_scope

    from core.research.materialize.consumer import MaterializeRequired

    fs = live_feature_set(paper)
    try:
        with scoring_model_role_context(MODEL_ROLE_LIVE), feature_set_scope(fs):
            table = _execution_arm_table(day_s, fs)
    except MaterializeRequired:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("live execution-suite score failed", exc_info=True)
        return False
    if not table or not (table.get("heads") or []):
        return False
    from core.backtest.replay_scoring import stamp_replay_arm_day

    stamp_replay_arm_day(rows, table, day_s)
    return True


def _execution_arm_table(day: str, feature_set: str) -> Dict[str, Any]:
    from core.research.yhat_factory_backtest import (
        enabled_replay_specs,
        score_enabled_replay_heads,
    )
    from core.universe.feature_sets import codes_for_feature_set

    specs = enabled_replay_specs()
    if not specs:
        logger.info("live execution suite has no enabled factory heads")
        return {}
    run_key = tuple(
        sorted(
            (str(s.get("head") or ""), str(s.get("run_id") or ""))
            for s in specs
        )
    )
    codes = [str(c).strip() for c in codes_for_feature_set(feature_set, fetch=False) if str(c).strip()]
    cache_key = (day, feature_set, run_key, len(codes))
    if _SCORE_CACHE.get("key") == cache_key and isinstance(_SCORE_CACHE.get("table"), dict):
        return _SCORE_CACHE["table"]
    from core.research.portfolio_bars import load_portfolio_stock_bars

    bars, _failed, _sym = load_portfolio_stock_bars(
        codes,
        lookback=120,
        fetch_fundamentals=False,
        offline_ok=True,
    )
    table = score_enabled_replay_heads(bars, only_day=day)
    _SCORE_CACHE["key"] = cache_key
    _SCORE_CACHE["table"] = table
    return table
