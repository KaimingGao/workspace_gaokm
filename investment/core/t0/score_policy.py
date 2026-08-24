"""多层 ŷ 驱动的 A 股底仓做 T 策略（dual_y）。

角色（PIT）：
  y_trade  — |ŷ_trade| 下限（预期日波动幅度）/ 额度缩放
  y_eod    — T−1 冻结先验（仅冲突检测）
  y_τ      — 盘中主方向（开→收）
  y_on     — 尾盘是否强制回补
  y_nowcast— 影子置信（默认可记录，不改方向）

选向分数默认**即时算**（开盘决策信息集：昨收因子 + 今开缺口），
不依赖 score_ledger / 分池簿冻结快照；账本与簿仅作可选兜底。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 默认阈值（ŷ 为百分比点；可用 rules 覆盖）
DEFAULT_TRADE_FLOOR = 0.15
DEFAULT_EOD_PRIOR = 0.35
DEFAULT_TAU_ENTER = 0.25
DEFAULT_ON_RISK = 0.80
DEFAULT_ON_ALLOW = 1.20
DEFAULT_RATIO_BOOST_CAP = 1.25
DEFAULT_RATIO_CUT = 0.75

# dual_y 下 y_τ 符号 → 正/反 T 映射（回测对照）
# scalp: y_τ>0→正T（高抛低吸启发式，现网默认）
# trend: y_τ>0→反T（趋势跟随：涨预期先买后卖）
# fixed_long / fixed_reverse: 忽略 y_τ 符号，固定方向（仍过 |y_τ| 门槛）
Y_TAU_MAP_DEFAULT = "scalp"
Y_TAU_MAP_CHOICES = ("scalp", "trend", "fixed_long", "fixed_reverse")
Y_TAU_MAP_LABELS = {
    "scalp": "高抛低吸（y_τ>0→正T）",
    "trend": "趋势跟随（y_τ>0→反T）",
    "fixed_long": "固定正T",
    "fixed_reverse": "固定反T",
}

# compute | live_book | ledger
DEFAULT_Y_SCORE_SOURCE = "compute"
_MIN_HIST_BARS = 16
# 做 T 回测：评估窗与因子窗分离；warmup 仅供 hist_prior，不进成交明细
T0_BACKTEST_SCORE_WARMUP = max(_MIN_HIST_BARS + 8, 40)

# 进程内轻量缓存：回测逐日重算时复用模型句柄
_MODEL_CACHE: Dict[str, Any] = {}


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


def normalize_y_trade_floor(raw: Any) -> float:
    """|y_trade| 下限（收益百分点）；旧配置负值加载时取 abs。"""
    try:
        val = float(DEFAULT_TRADE_FLOOR if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        val = DEFAULT_TRADE_FLOOR
    return max(0.0, min(abs(val), 5.0))


def trade_mag_floor(cfg: dict) -> float:
    return normalize_y_trade_floor(cfg.get("y_trade_floor"))


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    try:
        v = cfg.get(key)
        if v is None or v == "":
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def scale_t0_ratio(base_ratio: float, scores: dict, cfg: dict) -> float:
    """按 |y_trade| 缩放做T比例；缺分则不改。"""
    y_trade = _f(scores.get("y_trade"))
    if y_trade is None:
        return float(base_ratio)
    floor = trade_mag_floor(cfg)
    boost_cap = _cfg_float(cfg, "y_ratio_boost_cap", DEFAULT_RATIO_BOOST_CAP)
    cut = _cfg_float(cfg, "y_ratio_cut", DEFAULT_RATIO_CUT)
    r = float(base_ratio)
    mag = abs(y_trade)
    if mag < floor:
        return max(0.05, r * cut)
    span = max(0.5, floor + 1.0)
    t = min(1.0, max(0.0, (mag - floor) / span))
    return min(1.0, r * (1.0 + (boost_cap - 1.0) * t))


def normalize_y_tau_map(raw: Any) -> str:
    mode = str(raw or Y_TAU_MAP_DEFAULT).strip().lower()
    aliases = {
        "follow": "trend",
        "momentum": "trend",
        "invert": "trend",
        "long": "fixed_long",
        "always_long": "fixed_long",
        "reverse": "fixed_reverse",
        "always_reverse": "fixed_reverse",
    }
    mode = aliases.get(mode, mode)
    if mode not in Y_TAU_MAP_CHOICES:
        mode = Y_TAU_MAP_DEFAULT
    return mode


def direction_from_y_tau_sign(tau_sign: int, cfg: dict) -> str:
    """由 y_τ 符号（±1）与 y_tau_map 解析 long_t / reverse_t。"""
    mode = normalize_y_tau_map(cfg.get("y_tau_map"))
    if mode == "trend":
        return "reverse_t" if tau_sign > 0 else "long_t"
    if mode == "fixed_long":
        return "long_t"
    if mode == "fixed_reverse":
        return "reverse_t"
    return "long_t" if tau_sign > 0 else "reverse_t"


def resolve_dual_y_direction(
    *,
    scores: dict,
    cfg: dict,
    cash: float,
    shares: float,
) -> Dict[str, Any]:
    """dual_y 选向：|y_trade|下限 → 主方向(y_τ + y_tau_map) → 与 y_eod 冲突则跳过。"""
    trade_floor = trade_mag_floor(cfg)
    eod_prior = _cfg_float(cfg, "y_eod_prior", DEFAULT_EOD_PRIOR)
    tau_enter = _cfg_float(cfg, "y_tau_enter", DEFAULT_TAU_ENTER)
    block_conflict = bool(cfg.get("y_block_conflict", True))
    tau_map = normalize_y_tau_map(cfg.get("y_tau_map"))
    map_tag = Y_TAU_MAP_LABELS.get(tau_map, tau_map)

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
            "direction_reason": "dual_y：缺 y_eod/y_τ/y_trade（即时算分失败）",
            "features": features,
            "signal_skip": True,
        }

    if y_trade is not None and abs(y_trade) < trade_floor:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau if y_tau is not None else y_trade,
            "direction_reason": (
                f"dual_y：|y_trade|={abs(y_trade):.3f}%<{trade_floor}% 预期幅度不足"
            ),
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
            "direction_reason": f"dual_y：|y_τ|={abs(y_tau):.3f}%<{tau_enter}% 横盘跳过",
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
                f"dual_y[{tau_map}]：冲突 y_eod={y_eod:.3f}% vs y_τ={y_tau:.3f}%（先验≠盘中）跳过"
            ),
            "features": features,
            "signal_skip": True,
        }

    direction = direction_from_y_tau_sign(main, cfg)

    if direction == "reverse_t" and not (cash > 0 and shares > 0):
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": (
                f"dual_y[{tau_map}]：y_τ={y_tau:.3f}%→{direction} 但缺现金/仓"
            ),
            "features": features,
            "signal_skip": True,
        }

    nc_note = ""
    if y_nowcast is not None and (y_nowcast * y_tau) > 0 and abs(y_nowcast) >= abs(y_tau):
        nc_note = f"；nowcast={y_nowcast:.3f}%同向增强(影子)"
        features["nowcast_align"] = True
    else:
        features["nowcast_align"] = False

    return {
        "direction": direction,
        "skip": False,
        "direction_score": y_tau,
        "direction_reason": (
            f"dual_y[{tau_map}]：y_τ={y_tau:.3f}%→{direction}（{map_tag}）"
            + (f"；y_eod先验={prior:+d}" if prior else "")
            + (f"；y_trade={y_trade:.3f}%" if y_trade is not None else "")
            + nc_note
        ),
        "features": {**features, "y_tau_map": tau_map},
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
    trade_floor = trade_mag_floor(cfg)
    on_risk = _cfg_float(cfg, "y_on_risk", DEFAULT_ON_RISK)
    on_allow = _cfg_float(cfg, "y_on_allow", DEFAULT_ON_ALLOW)

    if y_trade is not None and abs(y_trade) < trade_floor:
        return {
            "must_cover": True,
            "reason": f"|y_trade|={abs(y_trade):.3f}%<{trade_floor}%强制回补",
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
            "reason": f"|y_on|={abs(y_on):.3f}%<allow{on_allow}%默认回补",
            "allow_overnight": False,
        }

    # |y_on| 很大：仅当方向与敞口一致才允许隔夜
    # 正T未回补 = 隔夜空头敞口 → 需要 y_on 显著为负（看跌隔夜）
    # 反T买了未卖旧 = 多头增量 → 需要 y_on 显著为正
    if direction == "long_t" and y_on <= -on_allow:
        return {
            "must_cover": False,
            "reason": f"y_on={y_on:.3f}%支持正T隔夜空头敞口",
            "allow_overnight": True,
        }
    if direction == "reverse_t" and y_on >= on_allow:
        return {
            "must_cover": False,
            "reason": f"y_on={y_on:.3f}%支持反T隔夜多头",
            "allow_overnight": True,
        }

    return {
        "must_cover": True,
        "reason": f"y_on={y_on:.3f}%与敞口方向不一致，强制回补",
        "allow_overnight": False,
    }


def load_scores_for_code_date(code: str, as_of: str) -> Dict[str, Optional[float]]:
    """从 score_ledger 取某日某票分数；没有则空（仅兜底）。"""
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


def open_decision_quote(
    day_bar: dict,
    prev_bar: Optional[dict] = None,
    *,
    code: str = "",
) -> Dict[str, Any]:
    """开盘决策报价：只用 open[T] / close[T−1]，不把收盘价当现价。"""
    o = _f((day_bar or {}).get("open"))
    pc = _f((day_bar or {}).get("prev_close"))
    if pc is None and isinstance(prev_bar, dict):
        pc = _f(prev_bar.get("close"))
    change = None
    if o is not None and pc is not None and pc > 0:
        change = round((float(o) / float(pc) - 1.0) * 100.0, 4)
    return {
        "success": True,
        "stock_code": str(code or "").strip() or None,
        "date": str((day_bar or {}).get("date") or "")[:10] or None,
        "trade_date": str((day_bar or {}).get("date") or "")[:10] or None,
        "open": o,
        "open_raw": o,
        "prev_close": pc,
        "price_raw": o,
        "change_raw": change,
    }


def _scoring_models() -> Tuple[Any, Dict[str, Any], Any]:
    """(rem_doc, cluster_models_by_code, global_return_model)。"""
    if _MODEL_CACHE.get("ok"):
        return (
            _MODEL_CACHE.get("rem"),
            _MODEL_CACHE.get("cluster") or {},
            _MODEL_CACHE.get("global_rm"),
        )
    rem = None
    try:
        from core.research.rem_ridge import load_rem_model

        rem = load_rem_model()
    except Exception:  # noqa: BLE001
        logger.debug("load rem model failed", exc_info=True)
    cluster: Dict[str, Any] = {}
    try:
        from core.signal.cluster.live import (
            filter_primary_cluster_models_by_code,
            load_cluster_return_models_by_code,
        )

        cluster = filter_primary_cluster_models_by_code(
            load_cluster_return_models_by_code() or {}
        ) or {}
    except Exception:  # noqa: BLE001
        logger.debug("load cluster return models failed", exc_info=True)
    global_rm = None
    try:
        from core.signal.return_score_store import load_return_model

        global_rm, _meta = load_return_model(prefer_active=True)
    except Exception:  # noqa: BLE001
        logger.debug("load global return model failed", exc_info=True)
    _MODEL_CACHE.clear()
    _MODEL_CACHE.update(
        {"ok": True, "rem": rem, "cluster": cluster, "global_rm": global_rm}
    )
    return rem, cluster, global_rm


def clear_score_model_cache() -> None:
    """测试 / 热更模型后清空缓存。"""
    _MODEL_CACHE.clear()


def compute_scores_from_bars(
    code: str,
    hist_bars: Sequence[dict],
    *,
    day_bar: Optional[dict] = None,
    quote: Optional[dict] = None,
    rem_model_doc: Any = None,
    return_models_by_code: Optional[Dict[str, Any]] = None,
    default_return_model: Any = None,
    fuse_intraday: bool = True,
    min_history: int = _MIN_HIST_BARS,
    horizon_days: int = 1,
    pool_gaps: Optional[Sequence[float]] = None,
    sector_gap_breadth: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """开盘决策信息集即时算 dual_y 分数（不读账本/簿）。

    ``hist_bars``：不含当日的日线（因子截止 T−1）。
    ``day_bar`` / ``quote``：提供 open[T]（缺口）；勿用收盘价冒充开盘决策现价。
    ``pool_gaps`` / ``sector_gap_breadth``：截面缺口（批量算分时传入，增强 ŷ_τ）。
    """
    raw = str(code or "").strip()
    hist = [b for b in (hist_bars or []) if isinstance(b, dict)]
    if not raw or len(hist) < max(8, int(min_history or _MIN_HIST_BARS)):
        return scores_from_item(None)

    day = day_bar if isinstance(day_bar, dict) else None
    q = quote if isinstance(quote, dict) else None
    if q is None and day is not None:
        q = open_decision_quote(day, hist[-1] if hist else None, code=raw)
    if q is None:
        return scores_from_item(None)

    try:
        from core.signal.cross_section_batch import score_window_as_item
        from core.signal.dual_score import attach_dual_score_pit
        from core.signal.return_score import apply_predicted_scores_by_model
        from core.signal.y_state import build_y_state
    except Exception:  # noqa: BLE001
        logger.debug("compute_scores_from_bars imports failed", exc_info=True)
        return scores_from_item(None)

    rem, cluster, global_rm = _scoring_models()
    if rem_model_doc is not None:
        rem = rem_model_doc
    models = return_models_by_code if return_models_by_code is not None else cluster
    default_rm = default_return_model if default_return_model is not None else global_rm

    try:
        item = score_window_as_item(
            raw,
            list(hist),
            horizon_days=max(1, int(horizon_days or 1)),
            quote=q,
        )
        if not item:
            return scores_from_item(None)
        scored = apply_predicted_scores_by_model(
            [item],
            models or {},
            default_model=default_rm,
        )
        item = scored[0] if scored else item
        if item.get("predicted_score") is not None and item.get("predicted_score_eod") is None:
            item["predicted_score_eod"] = item.get("predicted_score")
        gaps = [float(g) for g in (pool_gaps or []) if g is not None]
        if gaps:
            item["_pool_gaps"] = gaps
        if sector_gap_breadth is not None:
            try:
                item["sector_gap_breadth"] = float(sector_gap_breadth)
            except (TypeError, ValueError):
                pass
        attach_dual_score_pit(
            item,
            quote=q,
            bars=hist[-12:],
            rem_model_doc=rem,
            sector_gap_breadth=item.get("sector_gap_breadth"),
            fuse_intraday=bool(fuse_intraday),
        )
        try:
            st = build_y_state(item)
            if st.get("check") is not None:
                item["y_check"] = st.get("check")
            if st.get("eod_trust") is not None:
                item["eod_trust"] = st.get("eod_trust")
        except Exception:  # noqa: BLE001
            logger.debug("build_y_state in compute failed", exc_info=True)
        out = scores_from_item(item)
        out["_score_source"] = "compute"
        return out
    except Exception:  # noqa: BLE001
        logger.debug("compute_scores_from_bars failed for %s", raw, exc_info=True)
        return scores_from_item(None)


def _open_gap_pct(day_bar: Optional[dict], prev_bar: Optional[dict]) -> Optional[float]:
    if not isinstance(day_bar, dict):
        return None
    o = _f(day_bar.get("open"))
    pc = _f(day_bar.get("prev_close"))
    if pc is None and isinstance(prev_bar, dict):
        pc = _f(prev_bar.get("close"))
    if o is None or pc is None or pc <= 0:
        return None
    return (float(o) / float(pc) - 1.0) * 100.0


def compute_scores_map_from_bars(
    specs: Sequence[Dict[str, Any]],
    *,
    fuse_intraday: bool = True,
) -> Dict[str, Dict[str, Optional[float]]]:
    """批量开盘算分：共享模型缓存，并用截面 open 缺口作 pool_gaps。

    每个 spec: ``{code, hist_bars, day_bar?}``。
    """
    out: Dict[str, Dict[str, Optional[float]]] = {}
    rows: List[Tuple[str, Sequence[dict], Optional[dict], Optional[float]]] = []
    gaps: List[float] = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        code = str(spec.get("code") or spec.get("stock_code") or "").strip()
        hist = [b for b in (spec.get("hist_bars") or []) if isinstance(b, dict)]
        day = spec.get("day_bar") if isinstance(spec.get("day_bar"), dict) else None
        if day is None and len(hist) >= _MIN_HIST_BARS + 1:
            day = hist[-1]
            hist = hist[:-1]
        if not code or len(hist) < _MIN_HIST_BARS:
            continue
        g = _open_gap_pct(day, hist[-1] if hist else None)
        rows.append((code, hist, day, g))
        if g is not None:
            gaps.append(float(g))

    breadth = None
    if gaps:
        try:
            from core.event_prior import get_event_prior_cfg

            trigger = float(get_event_prior_cfg().get("gap_trigger_pct") or 2.0)
            hit = sum(1 for g in gaps if abs(g) >= trigger)
            breadth = hit / float(len(gaps))
        except Exception:  # noqa: BLE001
            logger.debug("pool breadth failed", exc_info=True)
            breadth = None

    # 预热模型，避免逐票重复 IO
    _scoring_models()
    for code, hist, day, _g in rows:
        sc = compute_scores_from_bars(
            code,
            hist,
            day_bar=day,
            fuse_intraday=fuse_intraday,
            pool_gaps=gaps or None,
            sector_gap_breadth=breadth,
        )
        if scores_have_any(sc):
            out[code] = sc
    return out


def compute_scores_live(
    code: str,
    *,
    bars: Optional[Sequence[dict]] = None,
    quote: Optional[dict] = None,
    fuse_intraday: bool = True,
) -> Dict[str, Optional[float]]:
    """纸面/盘中：优先用已有日线 PIT 算；否则 ``score_stock``；再否则空。"""
    raw = str(code or "").strip()
    if not raw:
        return scores_from_item(None)

    hist_list = [b for b in (bars or []) if isinstance(b, dict)]
    if len(hist_list) >= _MIN_HIST_BARS + 1:
        day = hist_list[-1]
        hist = hist_list[:-1]
        q = quote if isinstance(quote, dict) else open_decision_quote(day, hist[-1], code=raw)
        sc = compute_scores_from_bars(
            raw,
            hist,
            day_bar=day,
            quote=q,
            fuse_intraday=fuse_intraday,
        )
        if scores_have_any(sc):
            return sc

    try:
        from core.signal.score_stock import score_stock

        item = score_stock(
            raw,
            quote=quote if isinstance(quote, dict) else None,
            skip_sentiment=True,
            skip_fundamentals=True,
            quote_timeout=8.0,
        )
        if isinstance(item, dict) and item.get("success") is not False:
            out = scores_from_item(item)
            if scores_have_any(out):
                out["_score_source"] = "score_stock"
                return out
    except Exception:  # noqa: BLE001
        logger.debug("compute_scores_live score_stock failed", exc_info=True)
    return scores_from_item(None)


def _scores_from_live_book(code: str) -> Dict[str, Optional[float]]:
    try:
        from core.signal.cluster.live import load_active_cluster_book

        book_doc = load_active_cluster_book() or {}
        key = str(code or "").strip()
        for row in list(book_doc.get("scored_all") or []) + list(book_doc.get("book") or []):
            if not isinstance(row, dict):
                continue
            rc = str(row.get("stock_code") or row.get("code") or "").strip()
            if rc == key:
                sc = scores_from_item(row)
                if scores_have_any(sc):
                    sc["_score_source"] = "live_book"
                    return sc
    except Exception:  # noqa: BLE001
        logger.debug("live book score lookup failed", exc_info=True)
    return scores_from_item(None)


def resolve_y_score_source(cfg: Optional[dict] = None) -> str:
    raw = str((cfg or {}).get("y_score_source") or DEFAULT_Y_SCORE_SOURCE).strip().lower()
    if raw in {"book", "cluster", "cluster_book"}:
        return "live_book"
    if raw in {"ledger", "score_ledger", "freeze"}:
        return "ledger"
    if raw in {"compute", "pit", "live", "realtime", "on_the_fly"}:
        return "compute"
    return DEFAULT_Y_SCORE_SOURCE


def resolve_scores_for_code(
    code: str,
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    day_bar: Optional[dict] = None,
    quote: Optional[dict] = None,
    as_of: Optional[str] = None,
    source: str = DEFAULT_Y_SCORE_SOURCE,
    fuse_intraday: bool = True,
    allow_fallback: bool = True,
    pool_gaps: Optional[Sequence[float]] = None,
    sector_gap_breadth: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """按 ``source`` 解析 dual_y 分数；默认即时算。

    ``compute`` 失败时仅回退 live 簿（不读冻结账本，避免半日污染快照）。
    """
    raw = str(code or "").strip()
    if not raw:
        return scores_from_item(None)
    src = resolve_y_score_source({"y_score_source": source})

    if src == "compute":
        hist = list(hist_bars or [])
        day = day_bar if isinstance(day_bar, dict) else None
        if day is None and hist:
            if len(hist) >= _MIN_HIST_BARS + 1:
                day = hist[-1]
                hist = hist[:-1]
        sc = compute_scores_from_bars(
            raw,
            hist,
            day_bar=day,
            quote=quote,
            fuse_intraday=fuse_intraday,
            pool_gaps=pool_gaps,
            sector_gap_breadth=sector_gap_breadth,
        )
        if scores_have_any(sc):
            return sc
        if not allow_fallback:
            return sc
        return _scores_from_live_book(raw)

    if src == "live_book":
        sc = _scores_from_live_book(raw)
        if scores_have_any(sc) or not allow_fallback:
            return sc
        return resolve_scores_for_code(
            raw,
            hist_bars=hist_bars,
            day_bar=day_bar,
            quote=quote,
            as_of=as_of,
            source="compute",
            fuse_intraday=fuse_intraday,
            allow_fallback=False,
            pool_gaps=pool_gaps,
            sector_gap_breadth=sector_gap_breadth,
        )

    # ledger（显式对照 / 旧路径）
    day_key = str(as_of or (day_bar or {}).get("date") or "")[:10]
    sc = load_scores_for_code_date(raw, day_key) if day_key else scores_from_item(None)
    if scores_have_any(sc):
        sc = dict(sc)
        sc["_score_source"] = "ledger"
        return sc
    if allow_fallback:
        return resolve_scores_for_code(
            raw,
            hist_bars=hist_bars,
            day_bar=day_bar,
            quote=quote,
            as_of=as_of,
            source="compute",
            fuse_intraday=fuse_intraday,
            allow_fallback=False,
            pool_gaps=pool_gaps,
            sector_gap_breadth=sector_gap_breadth,
        )
    return sc


def load_scores_map_for_codes(
    codes: Sequence[str],
    *,
    as_of: Optional[str] = None,
    prefer_live_book: bool = False,
    source: Optional[str] = None,
    bars_by_code: Optional[Dict[str, Sequence[dict]]] = None,
    hist_bars_by_code: Optional[Dict[str, Sequence[dict]]] = None,
    day_bars_by_code: Optional[Dict[str, dict]] = None,
    allow_fallback: bool = True,
) -> Dict[str, Dict[str, Optional[float]]]:
    """批量取 dual_y 分数。

    默认 ``source=compute``：用日线批量即时算（截面缺口共享）。
    ``prefer_live_book=True`` 仅兼容旧调用（等价 source=live_book）。
    """
    out: Dict[str, Dict[str, Optional[float]]] = {}
    want = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not want:
        return out

    if source is None:
        src = "live_book" if prefer_live_book else DEFAULT_Y_SCORE_SOURCE
    else:
        src = resolve_y_score_source({"y_score_source": source})

    if src == "compute" and (hist_bars_by_code or bars_by_code or day_bars_by_code):
        specs: List[Dict[str, Any]] = []
        for code in want:
            hist = None
            if hist_bars_by_code and code in hist_bars_by_code:
                hist = list(hist_bars_by_code.get(code) or [])
            elif bars_by_code and code in bars_by_code:
                hist = list(bars_by_code.get(code) or [])
            day = None
            if day_bars_by_code and code in day_bars_by_code:
                day = day_bars_by_code.get(code)
            specs.append({"code": code, "hist_bars": hist or [], "day_bar": day})
        computed = compute_scores_map_from_bars(specs, fuse_intraday=True)
        out.update(computed)
        if not allow_fallback:
            return out
        for code in want:
            if code in out:
                continue
            sc = _scores_from_live_book(code)
            if scores_have_any(sc):
                out[code] = sc
        return out

    for code in want:
        hist = None
        if hist_bars_by_code and code in hist_bars_by_code:
            hist = hist_bars_by_code.get(code)
        elif bars_by_code and code in bars_by_code:
            hist = bars_by_code.get(code)
        day = None
        if day_bars_by_code and code in day_bars_by_code:
            day = day_bars_by_code.get(code)
        sc = resolve_scores_for_code(
            code,
            hist_bars=hist,
            day_bar=day,
            as_of=as_of,
            source=src,
            fuse_intraday=True,
            allow_fallback=allow_fallback,
        )
        if scores_have_any(sc):
            out[code] = sc
    return out
