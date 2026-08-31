"""早盘调仓决策矩阵（L1 路径择时）与线性对照（L0）。

目标层与执行层分离：
  - 目标：y_fuse = 加权(y_trade, y_nowcast) 排序 Top-K / 定 w*
  - 过滤：开加仓要求 y_path、y_on 与方向同号；|y_path| 定 λ
  - 隔夜：y_on 另定收盘可留仓折扣 w*_close

默认 ``enabled=True``：观察池预演/落账与买卖腿闸均走矩阵；仍可用配置显式关闭闸。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 动作码
ACTION_OPEN = "open"
ACTION_ADD = "add"
ACTION_REDUCE = "reduce"
ACTION_EXIT = "exit"
ACTION_HOLD = "hold"
ACTION_SKIP = "skip_window"  # 本窗不调，目标保留
ACTION_PENDING_EXIT = "pending_exit"  # 必清但等更好卖点

MODE_LINEAR = "linear"  # L0：缺口立刻补
MODE_PATH = "path"  # L1：path + on 同号闸

DEFAULT_PATH_MATRIX: Dict[str, Any] = {
    "enabled": True,  # 手动观察池调仓始终用矩阵；买卖腿闸亦默认开
    "mode": MODE_PATH,  # path | linear
    "buy_floor": None,  # None → 调用方传入 / 回退 0.01
    "hold_floor": None,
    "path_enter": 0.1,  # |ŷ_path| 横盘门槛（%）
    "path_half": 0.5,  # ≥half → λ=0.5
    "path_full": 1.0,  # ≥full → λ=1.0
    "y_on_allow": 0.5,  # ≥allow → 隔夜满留
    "y_on_half": 0.1,  # ≥half 且 <allow → 隔夜半仓
    "fusion_w_trade": 0.5,  # y_trade 权重
    "fusion_w_nowcast": 0.5,  # y_nowcast 权重
    "sign_eps": 0.0,  # 同号判定；0=严格看符号
    "nowcast_sign_eps": 0.0,  # 兼容旧键，并入 sign_eps
    "require_nowcast_for_open": False,  # 废弃：开加不再单独卡 nc
    "require_path_on_same_sign": True,  # 开/加：path、on 与方向同号
    "allow_pending_exit_on_path_low": True,  # path>0 时清仓可推迟
    "min_weight_eps": 1e-4,  # |w-w*| 小于此视为已对齐
}


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


def get_path_matrix_cfg(
    timing_or_cfg: Optional[dict] = None,
    *,
    paper: Optional[dict] = None,
) -> Dict[str, Any]:
    """读 path_matrix 配置：rebalance_timing.path_matrix 优先，否则默认。"""
    out = dict(DEFAULT_PATH_MATRIX)
    raw: Any = None
    if isinstance(timing_or_cfg, dict):
        if isinstance(timing_or_cfg.get("path_matrix"), dict):
            raw = timing_or_cfg["path_matrix"]
        elif any(k in timing_or_cfg for k in DEFAULT_PATH_MATRIX):
            raw = timing_or_cfg
    if raw is None and isinstance(paper, dict):
        try:
            from core.paper.open_fill import get_rebalance_timing

            timing = get_rebalance_timing(paper) or {}
            if isinstance(timing.get("path_matrix"), dict):
                raw = timing["path_matrix"]
        except Exception:  # noqa: BLE001
            logger.debug("get_path_matrix_cfg timing failed", exc_info=True)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out and v is not None:
                out[k] = v
    mode = str(out.get("mode") or MODE_PATH).strip().lower()
    out["mode"] = MODE_LINEAR if mode in {"linear", "l0", "score_budget"} else MODE_PATH
    out["enabled"] = bool(out.get("enabled"))
    for key, default, lo, hi in (
        ("path_enter", 0.1, 0.01, 5.0),
        ("path_half", 0.5, 0.01, 5.0),
        ("path_full", 1.0, 0.01, 10.0),
        ("y_on_allow", 0.5, 0.0, 5.0),
        ("y_on_half", 0.1, -5.0, 5.0),
        ("fusion_w_trade", 0.5, 0.0, 1.0),
        ("fusion_w_nowcast", 0.5, 0.0, 1.0),
        ("sign_eps", 0.0, 0.0, 1.0),
        ("nowcast_sign_eps", 0.0, 0.0, 1.0),
        ("min_weight_eps", 1e-4, 0.0, 0.05),
    ):
        try:
            out[key] = max(lo, min(float(out.get(key, default)), hi))
        except (TypeError, ValueError):
            out[key] = float(default)
    # 旧 nowcast_sign_eps 并入 sign_eps（若未单独写 sign_eps）
    if abs(float(out.get("sign_eps") or 0.0)) < 1e-15 and float(
        out.get("nowcast_sign_eps") or 0.0
    ) > 0:
        out["sign_eps"] = float(out["nowcast_sign_eps"])
    wt = float(out["fusion_w_trade"])
    wn = float(out["fusion_w_nowcast"])
    s = wt + wn
    if s <= 1e-12:
        out["fusion_w_trade"], out["fusion_w_nowcast"] = 0.5, 0.5
    else:
        out["fusion_w_trade"], out["fusion_w_nowcast"] = wt / s, wn / s
    if float(out["path_enter"]) > float(out["path_half"]):
        out["path_half"] = float(out["path_enter"])
    if float(out["path_half"]) > float(out["path_full"]):
        out["path_full"] = float(out["path_half"])
    out["require_nowcast_for_open"] = bool(out.get("require_nowcast_for_open", False))
    out["require_path_on_same_sign"] = bool(out.get("require_path_on_same_sign", True))
    out["allow_pending_exit_on_path_low"] = bool(
        out.get("allow_pending_exit_on_path_low", True)
    )
    return out


def fuse_trade_nowcast(
    y_trade: Optional[float],
    y_nowcast: Optional[float],
    *,
    w_trade: float = 0.5,
    w_nowcast: float = 0.5,
) -> Optional[float]:
    """加权融合；缺一侧则用另一侧；都缺则 None。"""
    wt = max(0.0, float(w_trade))
    wn = max(0.0, float(w_nowcast))
    t = _f(y_trade)
    n = _f(y_nowcast)
    if t is None and n is None:
        return None
    if t is None:
        return float(n)
    if n is None:
        return float(t)
    s = wt + wn
    if s <= 1e-12:
        return 0.5 * float(t) + 0.5 * float(n)
    return (wt * float(t) + wn * float(n)) / s


def _same_sign(
    a: Optional[float],
    b: Optional[float],
    *,
    eps: float = 0.0,
) -> Tuple[bool, str]:
    """两数同号（相对 eps 中性带）。缺一则失败。"""
    if a is None or b is None:
        return False, "缺分数无法同号"
    ea = float(eps)
    fa, fb = float(a), float(b)
    if abs(fa) <= ea or abs(fb) <= ea:
        return False, f"|分|≤{ea} 中性"
    if (fa > 0) == (fb > 0):
        return True, "同号"
    return False, "异号"


def scores_from_rebalance_item(item: Optional[dict]) -> Dict[str, Optional[float]]:
    """从调仓行 / signal_item 抽四分数（字段与 dual_y 对齐）。"""
    if not isinstance(item, dict):
        return {"y_trade": None, "y_path": None, "y_nowcast": None, "y_on": None}

    y_trade = _f(item.get("y_trade"))
    if y_trade is None:
        y_trade = _f(item.get("predicted_score_blend"))
    if y_trade is None:
        y_trade = _f(item.get("decision_score"))
    if y_trade is None:
        y_trade = _f(item.get("predicted_score"))
    # 禁止 heuristic 0–100 冒充
    if y_trade is not None and abs(y_trade) > 20.0:
        y_trade = None

    y_path = _f(item.get("y_path"))
    if y_path is None:
        y_path = _f(item.get("predicted_score_path"))

    y_nowcast = _f(item.get("y_nowcast"))
    if y_nowcast is None:
        y_nowcast = _f(item.get("predicted_score_nowcast"))

    y_on = _f(item.get("y_on"))
    if y_on is None:
        y_on = _f(item.get("predicted_score_on"))

    return {
        "y_trade": y_trade,
        "y_path": y_path,
        "y_nowcast": y_nowcast,
        "y_on": y_on,
    }


def path_execution_lambda(y_path: Optional[float], cfg: dict) -> Tuple[float, str]:
    """|y_path| → 本窗执行力度 λ∈{0,0.5,1}。"""
    enter = float(cfg.get("path_enter") or 0.1)
    half = float(cfg.get("path_half") or 0.5)
    full = float(cfg.get("path_full") or 1.0)
    if y_path is None:
        return 0.0, "缺 y_path"
    mag = abs(float(y_path))
    if mag < enter:
        return 0.0, f"|y_path|={mag:.3f}%<{enter}% 横盘"
    if mag >= full:
        return 1.0, f"|y_path|={mag:.3f}%≥{full}% 全量"
    return 0.5, f"|y_path|={mag:.3f}% 半量"


def overnight_scale(y_on: Optional[float], cfg: dict) -> Tuple[float, str]:
    """隔夜可留仓比例：满 / 半 / 零。"""
    allow = float(cfg.get("y_on_allow") or 0.5)
    half = float(cfg.get("y_on_half") if cfg.get("y_on_half") is not None else 0.1)
    if y_on is None:
        return 0.0, "缺 y_on → 不隔夜"
    if float(y_on) >= allow:
        return 1.0, f"y_on={y_on:.3f}%≥allow{allow}%"
    if float(y_on) >= half:
        return 0.5, f"y_on={y_on:.3f}% 半仓隔夜"
    return 0.0, f"y_on={y_on:.3f}%<{half}% 不隔夜"


def _same_sign_confirm(
    y_trade: Optional[float],
    y_nowcast: Optional[float],
    *,
    eps: float,
    require: bool,
) -> Tuple[bool, str]:
    if not require:
        return True, "nowcast 未强制"
    if y_nowcast is None:
        return False, "缺 y_nowcast"
    if y_trade is None:
        return False, "缺 y_trade"
    if abs(float(y_nowcast)) <= float(eps) and float(eps) > 0:
        return False, f"|y_nowcast|≤{eps} 中性"
    if (float(y_trade) > 0) == (float(y_nowcast) > 0):
        return True, "y_nowcast 与 y_trade 同号"
    return False, f"y_nowcast={y_nowcast:.3f} 与 y_trade={y_trade:.3f} 异号"


def _floors(cfg: dict, *, buy_floor: Optional[float], hold_floor: Optional[float]) -> Tuple[float, float]:
    bf = _f(buy_floor)
    if bf is None:
        bf = _f(cfg.get("buy_floor"))
    if bf is None:
        bf = 0.01
    hf = _f(hold_floor)
    if hf is None:
        hf = _f(cfg.get("hold_floor"))
    if hf is None:
        hf = min(float(bf), 0.01)
    # hold ≤ buy
    if float(hf) > float(bf):
        hf = float(bf)
    return float(bf), float(hf)


def resolve_target_weight(
    *,
    y_trade: Optional[float],
    w_day: float,
    cfg: dict,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
    hard_reject: bool = False,
    in_topk: bool = True,
    has_position: bool = False,
) -> Dict[str, Any]:
    """目标层：日内 w* 与收盘 w*_close（含 y_on 折扣前由调用方再乘 overnight）。

    ``w_day``：调用方已按 score_budget / 等权算好的候选目标（未持有且未过门槛时可为 0）。
    """
    bf, hf = _floors(cfg, buy_floor=buy_floor, hold_floor=hold_floor)
    w_day = max(0.0, float(w_day or 0.0))
    if hard_reject:
        return {
            "w_star": 0.0,
            "w_star_day": 0.0,
            "reason": "hard_reject",
            "buy_floor": bf,
            "hold_floor": hf,
        }
    if y_trade is None:
        # 无分：有仓则保持观察（不主动改目标），无仓不开
        return {
            "w_star": float(w_day) if has_position else 0.0,
            "w_star_day": float(w_day) if has_position else 0.0,
            "reason": "缺 y_trade",
            "buy_floor": bf,
            "hold_floor": hf,
        }
    yt = float(y_trade)
    if yt < hf:
        return {
            "w_star": 0.0,
            "w_star_day": 0.0,
            "reason": f"y_trade={yt:.3f}%<{hf}% hold",
            "buy_floor": bf,
            "hold_floor": hf,
        }
    if has_position:
        # 已持：未破 hold 则用传入 w_day（可低于开仓门槛的减仓目标）
        return {
            "w_star": w_day,
            "w_star_day": w_day,
            "reason": f"持仓目标 w_day={w_day:.4f}",
            "buy_floor": bf,
            "hold_floor": hf,
        }
    if not in_topk or yt < bf:
        return {
            "w_star": 0.0,
            "w_star_day": 0.0,
            "reason": (
                f"未准入 Top-K" if not in_topk else f"y_trade={yt:.3f}%<{bf}% buy"
            ),
            "buy_floor": bf,
            "hold_floor": hf,
        }
    return {
        "w_star": w_day,
        "w_star_day": w_day,
        "reason": f"新开目标 w_day={w_day:.4f}",
        "buy_floor": bf,
        "hold_floor": hf,
    }


def resolve_rebalance_action(
    *,
    y_trade: Optional[float] = None,
    y_path: Optional[float] = None,
    y_nowcast: Optional[float] = None,
    y_on: Optional[float] = None,
    w: float = 0.0,
    w_star_day: float = 0.0,
    in_topk: bool = True,
    hard_reject: bool = False,
    cfg: Optional[dict] = None,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """决议单票早盘动作。

    Parameters
    ----------
    w : 当前权重（0–1 或 0–100 均可，与 w_star_day 同尺度）
    w_star_day : 目标层候选仓（score_budget 结果）；破 hold / 未准入时内部压 0
    """
    rules = get_path_matrix_cfg(cfg)
    use_mode = str(mode or rules.get("mode") or MODE_PATH).strip().lower()
    if use_mode in {"linear", "l0", "score_budget"}:
        use_mode = MODE_LINEAR
    else:
        use_mode = MODE_PATH

    bf, hf = _floors(rules, buy_floor=buy_floor, hold_floor=hold_floor)
    has_pos = float(w or 0.0) > float(rules["min_weight_eps"])
    y_fuse = fuse_trade_nowcast(
        y_trade,
        y_nowcast,
        w_trade=float(rules.get("fusion_w_trade") or 0.5),
        w_nowcast=float(rules.get("fusion_w_nowcast") or 0.5),
    )
    # 目标层门槛用融合分；缺融合时回退 y_trade
    y_stance = y_fuse if y_fuse is not None else y_trade
    tgt = resolve_target_weight(
        y_trade=y_stance,
        w_day=float(w_star_day or 0.0),
        cfg=rules,
        buy_floor=bf,
        hold_floor=hf,
        hard_reject=bool(hard_reject),
        in_topk=bool(in_topk),
        has_position=has_pos,
    )
    w_star = float(tgt["w_star"])
    on_scale, on_note = overnight_scale(y_on, rules)
    w_close = round(w_star * on_scale, 6)

    eps = float(rules["min_weight_eps"])
    gap = float(w_star) - float(w or 0.0)
    scores = {
        "y_trade": y_trade,
        "y_path": y_path,
        "y_nowcast": y_nowcast,
        "y_on": y_on,
        "y_fuse": y_fuse,
    }
    base = {
        "mode": use_mode,
        "action": ACTION_HOLD,
        "lambda": 1.0 if use_mode == MODE_LINEAR else 0.0,
        "w": float(w or 0.0),
        "w_star": w_star,
        "w_star_day": float(tgt["w_star_day"]),
        "w_close": w_close,
        "overnight_scale": on_scale,
        "delta_w": 0.0,
        "buy_floor": bf,
        "hold_floor": hf,
        "reason": "",
        "notes": [str(tgt.get("reason") or ""), on_note],
        "scores": scores,
        "execute": True,
    }

    # —— 硬清仓义务 ——
    must_exit = hard_reject or (y_stance is not None and float(y_stance) < hf) or (
        has_pos and w_star <= eps and float(w or 0.0) > eps
    )
    # 双空：融合分<0 且 on<0 → 目标已是 0；强调清仓
    if (
        has_pos
        and y_stance is not None
        and y_on is not None
        and float(y_stance) < 0
        and float(y_on) < 0
    ):
        must_exit = True
        w_star = 0.0
        w_close = 0.0
        base["w_star"] = 0.0
        base["w_close"] = 0.0
        base["notes"].append("y_fuse<0 且 y_on<0 双空清仓")

    if use_mode == MODE_LINEAR:
        return _resolve_linear(
            base,
            w=float(w or 0.0),
            w_star=w_star,
            w_close=w_close,
            must_exit=must_exit,
            has_pos=has_pos,
            eps=eps,
            y_trade=y_stance,
            bf=bf,
            hf=hf,
        )

    return _resolve_path(
        base,
        rules=rules,
        w=float(w or 0.0),
        w_star=w_star,
        w_close=w_close,
        must_exit=must_exit,
        has_pos=has_pos,
        eps=eps,
        gap=gap,
        y_trade=y_stance,
        y_path=y_path,
        y_nowcast=y_nowcast,
        y_on=y_on,
        bf=bf,
        hf=hf,
    )


def _resolve_linear(
    base: dict,
    *,
    w: float,
    w_star: float,
    w_close: float,
    must_exit: bool,
    has_pos: bool,
    eps: float,
    y_trade: Optional[float],
    bf: float,
    hf: float,
) -> Dict[str, Any]:
    """L0：忽略 path/nowcast，按缺口立刻推（隔夜用 w_close 若调用方在收盘）。"""
    out = dict(base)
    out["lambda"] = 1.0
    out["execute"] = True
    target = w_star  # 早盘用日内目标；收盘压仓由上层改用 w_close
    if must_exit or (has_pos and target <= eps):
        out["action"] = ACTION_EXIT
        out["delta_w"] = -w
        out["reason"] = f"线性清仓（hold={hf}%）"
        return out
    if abs(target - w) <= eps:
        out["action"] = ACTION_HOLD
        out["delta_w"] = 0.0
        out["reason"] = "线性已对齐目标"
        return out
    if target > w + eps:
        out["action"] = ACTION_ADD if has_pos else ACTION_OPEN
        out["delta_w"] = target - w
        out["reason"] = (
            f"线性{'加仓' if has_pos else '开仓'}→{target:.4f}"
            f"（y_trade={y_trade} buy≥{bf}%）"
        )
        return out
    out["action"] = ACTION_REDUCE
    out["delta_w"] = target - w  # 负
    out["reason"] = f"线性减仓→{target:.4f}"
    _ = w_close
    return out


def _resolve_path(
    base: dict,
    *,
    rules: dict,
    w: float,
    w_star: float,
    w_close: float,
    must_exit: bool,
    has_pos: bool,
    eps: float,
    gap: float,
    y_trade: Optional[float],
    y_path: Optional[float],
    y_nowcast: Optional[float],
    y_on: Optional[float],
    bf: float,
    hf: float,
) -> Dict[str, Any]:
    """L1：path/on 与方向同号过滤；|path| 定 λ。"""
    out = dict(base)
    lam, lam_note = path_execution_lambda(y_path, rules)
    out["notes"].append(lam_note)
    out["lambda"] = lam

    path_enter = float(rules["path_enter"])
    path_pos = y_path is not None and float(y_path) > path_enter
    path_neg = y_path is not None and float(y_path) < -path_enter
    sign_eps = float(rules.get("sign_eps") or 0.0)
    require_po = bool(rules.get("require_path_on_same_sign", True))
    # 方向：要加仓为正，要减/清为负
    direction = 1.0 if gap > eps else (-1.0 if gap < -eps else None)

    # 清仓义务
    if must_exit or (has_pos and w_star <= eps and w > eps):
        if path_neg and lam > 0:
            # 全量 λ→exit；半量本窗只卖掉一部分，剩余仍 pending
            if lam >= 1.0 - 1e-9:
                out["action"] = ACTION_EXIT
                out["delta_w"] = -w
            else:
                out["action"] = ACTION_REDUCE
                out["delta_w"] = -w * lam
            out["execute"] = True
            out["reason"] = f"清仓义务@path高点 λ={lam}"
            return out
        if path_pos and bool(rules.get("allow_pending_exit_on_path_low", True)):
            out["action"] = ACTION_PENDING_EXIT
            out["delta_w"] = 0.0
            out["execute"] = False
            out["lambda"] = 0.0
            out["reason"] = "必清但 path 仍在低点，本窗推迟卖出"
            return out
        # 横盘或缺 path：义务优先，仍清
        out["action"] = ACTION_EXIT
        out["delta_w"] = -w
        out["lambda"] = 1.0
        out["execute"] = True
        out["reason"] = f"清仓义务（hold={hf}%），无合适 path 仍清"
        return out

    # 已对齐
    if abs(w_star - w) <= eps:
        # 隔夜需压仓：记在 notes，早盘不强制（收盘另跑）
        if w_close + eps < w and w_star > eps:
            out["notes"].append(
                f"收盘目标 w_close={w_close:.4f}<w，尾盘再减"
            )
        out["action"] = ACTION_HOLD
        out["delta_w"] = 0.0
        out["execute"] = False
        out["reason"] = "已对齐目标，不动"
        return out

    # 要加仓 / 开仓
    if gap > eps:
        if lam <= 0:
            out["action"] = ACTION_SKIP
            out["delta_w"] = 0.0
            out["execute"] = False
            out["reason"] = "欲加仓但 |path| 不足，本窗跳过"
            return out
        if not path_pos:
            out["action"] = ACTION_SKIP
            out["delta_w"] = 0.0
            out["execute"] = False
            out["reason"] = (
                f"欲加仓但 path 非探底（y_path={y_path}），本窗跳过"
            )
            return out
        if require_po:
            ok_path, path_note = _same_sign(direction, y_path, eps=sign_eps)
            out["notes"].append(f"path过滤:{path_note}")
            if not ok_path:
                out["action"] = ACTION_SKIP
                out["delta_w"] = 0.0
                out["execute"] = False
                out["reason"] = f"欲加仓但 path 不同号：{path_note}"
                return out
            ok_on, on_note = _same_sign(direction, y_on, eps=sign_eps)
            out["notes"].append(f"on过滤:{on_note}")
            if not ok_on:
                out["action"] = ACTION_SKIP
                out["delta_w"] = 0.0
                out["execute"] = False
                out["reason"] = f"欲加仓但 y_on 不同号：{on_note}"
                return out
        # 兼容：若显式打开旧 nowcast 闸
        if bool(rules.get("require_nowcast_for_open")):
            ok_nc, nc_note = _same_sign(y_trade, y_nowcast, eps=sign_eps)
            out["notes"].append(f"nc过滤:{nc_note}")
            if not ok_nc:
                out["action"] = ACTION_SKIP
                out["delta_w"] = 0.0
                out["execute"] = False
                out["reason"] = f"欲加仓但 nowcast 未确认：{nc_note}"
                return out
        delta = lam * gap
        out["action"] = ACTION_ADD if has_pos else ACTION_OPEN
        out["delta_w"] = delta
        out["execute"] = True
        out["reason"] = (
            f"{'加仓' if has_pos else '开仓'}@path低点 λ={lam} "
            f"Δw={delta:.4f}（buy≥{bf}%）"
        )
        return out

    # 要减仓（目标仍 >0）
    if gap < -eps:
        need = w - w_star
        if lam <= 0 or not path_neg:
            out["action"] = ACTION_SKIP
            out["delta_w"] = 0.0
            out["execute"] = False
            out["reason"] = "欲减仓但非 path 高点，本窗跳过"
            return out
        if require_po:
            ok_path, path_note = _same_sign(direction, y_path, eps=sign_eps)
            out["notes"].append(f"path过滤:{path_note}")
            if not ok_path:
                out["action"] = ACTION_SKIP
                out["delta_w"] = 0.0
                out["execute"] = False
                out["reason"] = f"欲减仓但 path 不同号：{path_note}"
                return out
            ok_on, on_note = _same_sign(direction, y_on, eps=sign_eps)
            out["notes"].append(f"on过滤:{on_note}")
            if not ok_on:
                out["action"] = ACTION_SKIP
                out["delta_w"] = 0.0
                out["execute"] = False
                out["reason"] = f"欲减仓但 y_on 不同号：{on_note}"
                return out
        else:
            # 旧走弱确认（未开同号闸时）
            weaken = False
            if y_on is not None and float(y_on) < 0:
                weaken = True
                out["notes"].append("y_on<0 支持减仓")
            if (
                y_nowcast is not None
                and y_trade is not None
                and float(y_nowcast) < float(y_trade)
            ):
                weaken = True
                out["notes"].append("y_nowcast<y_trade 走弱")
            if not weaken and y_trade is not None and float(y_trade) < bf:
                weaken = True
                out["notes"].append("y_trade 低于 buy_floor，弱持减仓")
            if not weaken:
                out["action"] = ACTION_SKIP
                out["delta_w"] = 0.0
                out["execute"] = False
                out["reason"] = "path 高点但无走弱确认，本窗不减"
                return out
        delta = -lam * need
        out["action"] = ACTION_REDUCE
        out["delta_w"] = delta
        out["execute"] = True
        out["reason"] = f"减仓@path高点 λ={lam} Δw={delta:.4f}"
        return out

    out["action"] = ACTION_HOLD
    out["reason"] = "无操作"
    out["execute"] = False
    return out


def resolve_rebalance_action_from_item(
    item: Optional[dict],
    *,
    w: float = 0.0,
    w_star_day: float = 0.0,
    in_topk: bool = True,
    cfg: Optional[dict] = None,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """从调仓行决议；hard_reject 读 item 字段。"""
    sc = scores_from_rebalance_item(item)
    hard = bool((item or {}).get("hard_reject")) if isinstance(item, dict) else False
    return resolve_rebalance_action(
        y_trade=sc["y_trade"],
        y_path=sc["y_path"],
        y_nowcast=sc["y_nowcast"],
        y_on=sc["y_on"],
        w=w,
        w_star_day=w_star_day,
        in_topk=in_topk,
        hard_reject=hard,
        cfg=cfg,
        buy_floor=buy_floor,
        hold_floor=hold_floor,
        mode=mode,
    )


def compare_linear_vs_path(
    *,
    y_trade: Optional[float],
    y_path: Optional[float],
    y_nowcast: Optional[float],
    y_on: Optional[float],
    w: float,
    w_star_day: float,
    in_topk: bool = True,
    hard_reject: bool = False,
    cfg: Optional[dict] = None,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
) -> Dict[str, Any]:
    """同输入下 L0 / L1 对照。"""
    rules = get_path_matrix_cfg(cfg)
    linear = resolve_rebalance_action(
        y_trade=y_trade,
        y_path=y_path,
        y_nowcast=y_nowcast,
        y_on=y_on,
        w=w,
        w_star_day=w_star_day,
        in_topk=in_topk,
        hard_reject=hard_reject,
        cfg=rules,
        buy_floor=buy_floor,
        hold_floor=hold_floor,
        mode=MODE_LINEAR,
    )
    path = resolve_rebalance_action(
        y_trade=y_trade,
        y_path=y_path,
        y_nowcast=y_nowcast,
        y_on=y_on,
        w=w,
        w_star_day=w_star_day,
        in_topk=in_topk,
        hard_reject=hard_reject,
        cfg=rules,
        buy_floor=buy_floor,
        hold_floor=hold_floor,
        mode=MODE_PATH,
    )
    return {
        "linear": linear,
        "path": path,
        "disagree": linear.get("action") != path.get("action")
        or abs(float(linear.get("delta_w") or 0) - float(path.get("delta_w") or 0))
        > float(rules["min_weight_eps"]),
    }


def plan_book_actions(
    items: Sequence[dict],
    *,
    weight_by_code: Optional[Dict[str, float]] = None,
    w_star_by_code: Optional[Dict[str, float]] = None,
    top_codes: Optional[Sequence[str]] = None,
    cfg: Optional[dict] = None,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    """批量决议（影子/预演）。"""
    rules = get_path_matrix_cfg(cfg)
    use_mode = mode or rules.get("mode")
    held = weight_by_code if isinstance(weight_by_code, dict) else {}
    stars = w_star_by_code if isinstance(w_star_by_code, dict) else {}
    top_set = {str(c).strip() for c in (top_codes or []) if str(c).strip()}
    rows: list = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if not code:
            continue
        in_top = (code in top_set) if top_set else True
        decision = resolve_rebalance_action_from_item(
            item,
            w=float(held.get(code) or 0.0),
            w_star_day=float(stars.get(code) or item.get("target_weight") or 0.0),
            in_topk=in_top,
            cfg=rules,
            buy_floor=buy_floor,
            hold_floor=hold_floor,
            mode=use_mode,
        )
        rows.append({"stock_code": code, **decision})
    by_action: Dict[str, int] = {}
    for r in rows:
        a = str(r.get("action") or "")
        by_action[a] = by_action.get(a, 0) + 1
    return {
        "mode": use_mode,
        "enabled": bool(rules.get("enabled")),
        "n": len(rows),
        "by_action": by_action,
        "rows": rows,
        "cfg": {
            k: rules[k]
            for k in (
                "path_enter",
                "path_half",
                "path_full",
                "y_on_allow",
                "y_on_half",
                "require_nowcast_for_open",
            )
        },
    }


def buy_execution_gate(
    item: Optional[dict],
    *,
    w: float = 0.0,
    w_star_day: float = 0.0,
    in_topk: bool = True,
    cfg: Optional[dict] = None,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
) -> Dict[str, Any]:
    """买腿闸：未 enable 则放行；path 模式下 skip/非开加则拦，λ 缩量。"""
    rules = get_path_matrix_cfg(cfg)
    out = {
        "enabled": bool(rules.get("enabled")),
        "allow": True,
        "lambda": 1.0,
        "action": None,
        "reason": "",
        "decision": None,
    }
    if not rules.get("enabled"):
        return out
    decision = resolve_rebalance_action_from_item(
        item,
        w=w,
        w_star_day=w_star_day,
        in_topk=in_topk,
        cfg=rules,
        buy_floor=buy_floor,
        hold_floor=hold_floor,
        mode=rules.get("mode"),
    )
    out["decision"] = decision
    out["action"] = decision.get("action")
    out["lambda"] = float(decision.get("lambda") or 0.0)
    out["reason"] = str(decision.get("reason") or "")
    act = str(decision.get("action") or "")
    if act in (ACTION_OPEN, ACTION_ADD) and decision.get("execute"):
        out["allow"] = True
        return out
    out["allow"] = False
    if not out["reason"]:
        out["reason"] = f"path_matrix 不买入（action={act}）"
    return out


def sell_execution_gate(
    item: Optional[dict],
    *,
    w: float,
    w_star_day: float = 0.0,
    in_topk: bool = True,
    cfg: Optional[dict] = None,
    buy_floor: Optional[float] = None,
    hold_floor: Optional[float] = None,
    sell_fraction: float = 1.0,
) -> Dict[str, Any]:
    """卖腿闸：pending_exit/skip → 本窗不卖；exit/reduce → 按 λ 缩卖出比例。"""
    rules = get_path_matrix_cfg(cfg)
    out = {
        "enabled": bool(rules.get("enabled")),
        "allow": True,
        "lambda": 1.0,
        "sell_fraction": float(sell_fraction),
        "action": None,
        "reason": "",
        "decision": None,
        "defer": False,
    }
    if not rules.get("enabled"):
        return out
    decision = resolve_rebalance_action_from_item(
        item,
        w=w,
        w_star_day=w_star_day,
        in_topk=in_topk,
        cfg=rules,
        buy_floor=buy_floor,
        hold_floor=hold_floor,
        mode=rules.get("mode"),
    )
    out["decision"] = decision
    out["action"] = decision.get("action")
    out["lambda"] = float(decision.get("lambda") or 0.0)
    out["reason"] = str(decision.get("reason") or "")
    act = str(decision.get("action") or "")
    if act == ACTION_PENDING_EXIT or (
        act == ACTION_SKIP and float(w or 0) > float(rules["min_weight_eps"])
    ):
        out["allow"] = False
        out["defer"] = True
        out["sell_fraction"] = 0.0
        return out
    if act in (ACTION_EXIT, ACTION_REDUCE) and decision.get("execute"):
        out["allow"] = True
        lam = max(0.0, min(1.0, float(decision.get("lambda") or 1.0)))
        # linear 全清；path 半量则只卖 λ
        if act == ACTION_EXIT and lam >= 1.0 - 1e-9:
            out["sell_fraction"] = 1.0
        elif act == ACTION_EXIT:
            out["sell_fraction"] = lam
        else:
            # reduce：|delta|/w
            w0 = max(float(w or 0), 1e-9)
            frac = abs(float(decision.get("delta_w") or 0.0)) / w0
            out["sell_fraction"] = max(0.0, min(1.0, frac))
        return out
    # hold 等：若上层已判定要卖（破 hold），path 未给卖信号则 defer
    if act in (ACTION_HOLD, ACTION_SKIP, ACTION_OPEN, ACTION_ADD):
        out["allow"] = False
        out["defer"] = True
        out["sell_fraction"] = 0.0
        if not out["reason"]:
            out["reason"] = f"path_matrix 本窗不卖（action={act}）"
        return out
    return out


__all__ = [
    "ACTION_ADD",
    "ACTION_EXIT",
    "ACTION_HOLD",
    "ACTION_OPEN",
    "ACTION_PENDING_EXIT",
    "ACTION_REDUCE",
    "ACTION_SKIP",
    "DEFAULT_PATH_MATRIX",
    "MODE_LINEAR",
    "MODE_PATH",
    "buy_execution_gate",
    "compare_linear_vs_path",
    "fuse_trade_nowcast",
    "get_path_matrix_cfg",
    "overnight_scale",
    "path_execution_lambda",
    "plan_book_actions",
    "resolve_rebalance_action",
    "resolve_rebalance_action_from_item",
    "resolve_target_weight",
    "scores_from_rebalance_item",
    "sell_execution_gate",
]
