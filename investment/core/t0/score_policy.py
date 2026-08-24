"""多层 ŷ 驱动的 A 股底仓做 T 策略（dual_y）。

角色（PIT）：
  y_trade  — 资格 / 额度缩放
  y_eod    — T−1 冻结先验（仅冲突检测）
  y_τ      — 盘中主方向（开→收）
  y_on     — 尾盘是否强制回补
  y_nowcast— 影子置信（默认可记录，不改方向）

不依赖跳空 auto / 开盘方向分 signal；与选股 ŷ 同仓但对齐各自标签。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

# 默认阈值（ŷ 为百分比点；可用 rules 覆盖）
DEFAULT_TRADE_FLOOR = -0.15
DEFAULT_EOD_PRIOR = 0.35
DEFAULT_TAU_ENTER = 0.25
DEFAULT_ON_RISK = 0.80
DEFAULT_ON_ALLOW = 1.20
DEFAULT_RATIO_BOOST_CAP = 1.25
DEFAULT_RATIO_CUT = 0.75


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def scores_from_item(item: Optional[dict]) -> Dict[str, Optional[float]]:
    """从 signal_item / 账本行 / insights 抽出做T用分数。"""
    if not isinstance(item, dict):
        return {
            "y_eod": None,
            "y_tau": None,
            "y_trade": None,
            "y_on": None,
            "y_nowcast": None,
            "y_check": None,
            "eod_trust": None,
        }
    y_eod = _f(item.get("y_eod"))
    if y_eod is None:
        y_eod = _f(item.get("predicted_score_eod"))
    if y_eod is None:
        y_eod = _f(item.get("yhat_eod"))

    y_tau = _f(item.get("y_tau"))
    if y_tau is None:
        y_tau = _f(item.get("predicted_score_tau"))
    if y_tau is None:
        y_tau = _f(item.get("score_rem"))
    if y_tau is None:
        y_tau = _f(item.get("yhat_tau"))

    y_trade = _f(item.get("y_trade"))
    if y_trade is None:
        y_trade = _f(item.get("predicted_score"))
    if y_trade is None:
        y_trade = _f(item.get("predicted_score_blend"))
    if y_trade is None:
        y_trade = _f(item.get("yhat"))
    # 禁止 heuristic 0–100 冒充 trade
    if y_trade is not None and abs(y_trade) > 20.0:
        y_trade = None

    y_on = _f(item.get("y_on"))
    if y_on is None:
        y_on = _f(item.get("predicted_score_on"))

    y_nowcast = _f(item.get("y_nowcast"))
    if y_nowcast is None:
        y_nowcast = _f(item.get("predicted_score_nowcast"))

    y_check = item.get("y_check")
    if y_check is not None:
        y_check = str(y_check)
    eod_trust = _f(item.get("eod_trust"))

    return {
        "y_eod": y_eod,
        "y_tau": y_tau,
        "y_trade": y_trade,
        "y_on": y_on,
        "y_nowcast": y_nowcast,
        "y_check": y_check,
        "eod_trust": eod_trust,
    }


def scores_from_ledger_row(row: Optional[dict]) -> Dict[str, Optional[float]]:
    if not isinstance(row, dict):
        return scores_from_item(None)
    # 账本字段名
    mapped = {
        "predicted_score_eod": row.get("yhat_eod") if row.get("yhat_eod") is not None else row.get("predicted_score_eod"),
        "predicted_score_tau": row.get("yhat_tau") if row.get("yhat_tau") is not None else row.get("predicted_score_tau"),
        "predicted_score": row.get("yhat") if row.get("yhat") is not None else row.get("predicted_score"),
        "predicted_score_on": row.get("yhat_on") if row.get("yhat_on") is not None else row.get("predicted_score_on"),
        "predicted_score_nowcast": row.get("yhat_nowcast")
        if row.get("yhat_nowcast") is not None
        else row.get("predicted_score_nowcast"),
        "y_check": row.get("y_check"),
        "eod_trust": row.get("eod_trust"),
    }
    return scores_from_item(mapped)


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    try:
        v = cfg.get(key)
        if v is None or v == "":
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def scale_t0_ratio(base_ratio: float, scores: dict, cfg: dict) -> float:
    """按 y_trade 缩放做T比例；缺分则不改。"""
    y_trade = _f(scores.get("y_trade"))
    if y_trade is None:
        return float(base_ratio)
    floor = _cfg_float(cfg, "y_trade_floor", DEFAULT_TRADE_FLOOR)
    boost_cap = _cfg_float(cfg, "y_ratio_boost_cap", DEFAULT_RATIO_BOOST_CAP)
    cut = _cfg_float(cfg, "y_ratio_cut", DEFAULT_RATIO_CUT)
    r = float(base_ratio)
    if y_trade < floor:
        return max(0.05, r * cut)
    # 高于 floor 越多略增，封顶
    span = max(0.5, abs(floor) + 1.0)
    t = min(1.0, max(0.0, (y_trade - floor) / span))
    return min(1.0, r * (1.0 + (boost_cap - 1.0) * t))


def resolve_dual_y_direction(
    *,
    scores: dict,
    cfg: dict,
    cash: float,
    shares: float,
) -> Dict[str, Any]:
    """dual_y 选向：资格(y_trade) → 主方向(y_τ) → 与 y_eod 冲突则跳过。"""
    trade_floor = _cfg_float(cfg, "y_trade_floor", DEFAULT_TRADE_FLOOR)
    eod_prior = _cfg_float(cfg, "y_eod_prior", DEFAULT_EOD_PRIOR)
    tau_enter = _cfg_float(cfg, "y_tau_enter", DEFAULT_TAU_ENTER)
    block_conflict = bool(cfg.get("y_block_conflict", True))

    y_eod = _f(scores.get("y_eod"))
    y_tau = _f(scores.get("y_tau"))
    y_trade = _f(scores.get("y_trade"))
    y_nowcast = _f(scores.get("y_nowcast"))
    y_check = scores.get("y_check")

    features = {
        "y_eod": y_eod,
        "y_tau": y_tau,
        "y_trade": y_trade,
        "y_on": _f(scores.get("y_on")),
        "y_nowcast": y_nowcast,
        "y_check": y_check,
    }

    if y_trade is None and y_tau is None and y_eod is None:
        return {
            "direction": None,
            "skip": True,
            "direction_score": None,
            "direction_reason": "dual_y：缺 y_eod/y_τ/y_trade 快照",
            "features": features,
            "signal_skip": True,
        }

    if y_trade is not None and y_trade < trade_floor:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_trade,
            "direction_reason": f"dual_y：y_trade={y_trade:.3f}<floor{trade_floor} 资格不足",
            "features": features,
            "signal_skip": True,
        }

    if block_conflict and y_check in {"conflict"}:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_trade,
            "direction_reason": "dual_y：y_check=conflict 禁止做T",
            "features": features,
            "signal_skip": True,
        }

    if y_tau is None:
        return {
            "direction": None,
            "skip": True,
            "direction_score": None,
            "direction_reason": "dual_y：缺 y_τ，无法定盘中方向",
            "features": features,
            "signal_skip": True,
        }

    if abs(y_tau) < tau_enter:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": f"dual_y：|y_τ|={abs(y_tau):.3f}<{tau_enter} 横盘跳过",
            "features": features,
            "signal_skip": True,
        }

    # 先验：仅用于冲突
    prior = 0
    if y_eod is not None:
        if y_eod >= eod_prior:
            prior = 1
        elif y_eod <= -eod_prior:
            prior = -1

    main = 1 if y_tau >= tau_enter else -1
    if prior != 0 and prior != main:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": (
                f"dual_y：冲突 y_eod={y_eod} vs y_τ={y_tau}（先验≠盘中）跳过"
            ),
            "features": features,
            "signal_skip": True,
        }

    if main < 0:
        if not (cash > 0 and shares > 0):
            return {
                "direction": None,
                "skip": True,
                "direction_score": y_tau,
                "direction_reason": f"dual_y：y_τ={y_tau:.3f}→反T 但缺现金/仓",
                "features": features,
                "signal_skip": True,
            }
        direction = "reverse_t"
    else:
        direction = "long_t"

    nc_note = ""
    if y_nowcast is not None and (y_nowcast * y_tau) > 0 and abs(y_nowcast) >= abs(y_tau):
        nc_note = f"；nowcast={y_nowcast:.3f}同向增强(影子)"
        features["nowcast_align"] = True
    else:
        features["nowcast_align"] = False

    return {
        "direction": direction,
        "skip": False,
        "direction_score": y_tau,
        "direction_reason": (
            f"dual_y：y_τ={y_tau:.3f}→{direction}"
            + (f"；y_eod先验={prior:+d}" if prior else "")
            + (f"；y_trade={y_trade:.3f}" if y_trade is not None else "")
            + nc_note
        ),
        "features": features,
        "signal_skip": False,
    }


def resolve_cover_policy(
    *,
    scores: dict,
    direction: Optional[str],
    cfg: dict,
) -> Dict[str, Any]:
    """尾盘回补：默认强制；仅当 y_on 强烈支持隔夜敞口时放行。"""
    if bool(cfg.get("must_cover_same_day")):
        return {
            "must_cover": True,
            "reason": "表单强制当日回补",
            "allow_overnight": False,
        }

    y_on = _f(scores.get("y_on"))
    y_trade = _f(scores.get("y_trade"))
    trade_floor = _cfg_float(cfg, "y_trade_floor", DEFAULT_TRADE_FLOOR)
    on_risk = _cfg_float(cfg, "y_on_risk", DEFAULT_ON_RISK)
    on_allow = _cfg_float(cfg, "y_on_allow", DEFAULT_ON_ALLOW)

    if y_trade is not None and y_trade < trade_floor:
        return {
            "must_cover": True,
            "reason": f"y_trade弱({y_trade:.3f})强制回补",
            "allow_overnight": False,
        }

    if y_on is None:
        return {
            "must_cover": True,
            "reason": "缺 y_on，默认强制回补",
            "allow_overnight": False,
        }

    if abs(y_on) < on_allow:
        # 中等隔夜预期：仍强制回补（稳健）
        if abs(y_on) >= on_risk:
            return {
                "must_cover": True,
                "reason": f"|y_on|={abs(y_on):.3f}≥risk{on_risk}强制回补",
                "allow_overnight": False,
            }
        return {
            "must_cover": True,
            "reason": f"|y_on|={abs(y_on):.3f}<allow{on_allow}默认回补",
            "allow_overnight": False,
        }

    # |y_on| 很大：仅当方向与敞口一致才允许隔夜
    # 正T未回补 = 隔夜空头敞口 → 需要 y_on 显著为负（看跌隔夜）
    # 反T买了未卖旧 = 多头增量 → 需要 y_on 显著为正
    if direction == "long_t" and y_on <= -on_allow:
        return {
            "must_cover": False,
            "reason": f"y_on={y_on:.3f}支持正T隔夜空头敞口",
            "allow_overnight": True,
        }
    if direction == "reverse_t" and y_on >= on_allow:
        return {
            "must_cover": False,
            "reason": f"y_on={y_on:.3f}支持反T隔夜多头",
            "allow_overnight": True,
        }

    return {
        "must_cover": True,
        "reason": f"y_on={y_on:.3f}与敞口方向不一致，强制回补",
        "allow_overnight": False,
    }


def load_scores_for_code_date(code: str, as_of: str) -> Dict[str, Optional[float]]:
    """从 score_ledger 取某日某票分数；没有则空。"""
    try:
        from core.score_ledger import load_ledger

        led = load_ledger(as_of)
        rows = led.get("rows") or []
        key = str(code or "").strip()
        for r in rows:
            if not isinstance(r, dict):
                continue
            rc = str(r.get("stock_code") or r.get("code") or "").strip()
            if rc == key:
                return scores_from_ledger_row(r)
    except Exception:  # noqa: BLE001
        logger.debug("load_scores_for_code_date failed", exc_info=True)
    return scores_from_item(None)


def scores_have_any(scores: Optional[dict]) -> bool:
    if not isinstance(scores, dict):
        return False
    return any(_f(scores.get(k)) is not None for k in ("y_eod", "y_tau", "y_trade", "y_on"))


def load_scores_map_for_codes(
    codes: Sequence[str],
    *,
    as_of: Optional[str] = None,
    prefer_live_book: bool = True,
) -> Dict[str, Dict[str, Optional[float]]]:
    """批量取 dual_y 分数：优先分池簿 → 当日账本 → 空。"""
    out: Dict[str, Dict[str, Optional[float]]] = {}
    want = {str(c).strip() for c in (codes or []) if str(c or "").strip()}
    if not want:
        return out

    if prefer_live_book:
        try:
            from core.signal.cluster.live import load_active_cluster_book

            book_doc = load_active_cluster_book() or {}
            book_rows = list(book_doc.get("scored_all") or []) + list(book_doc.get("book") or [])
            for row in book_rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("stock_code") or row.get("code") or "").strip()
                if code not in want or code in out:
                    continue
                sc = scores_from_item(row)
                if scores_have_any(sc):
                    out[code] = sc
        except Exception:  # noqa: BLE001
            logger.debug("load_scores_map live book failed", exc_info=True)

    missing = [c for c in want if c not in out]
    day = str(as_of or "").strip()[:10]
    if missing and day:
        for code in missing:
            sc = load_scores_for_code_date(code, day)
            if scores_have_any(sc):
                out[code] = sc
    return out
