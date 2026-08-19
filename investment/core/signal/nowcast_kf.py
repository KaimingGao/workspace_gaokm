"""Fixed-event nowcast：同一 T 收、多信息集 Kalman 更新剩余收益。

状态 x(τ) = close[T]/price[τ]−1（%）。各头独立训练；本模块只在决策层融合。
不改写 predicted_score（ŷ_EOD）。规范见 docs/predicted-score-chain.md §2.5 nowcast。

顺序滤波：EOD 先验 → open →（可选）当前分钟 τ。同一 ŷ_τ 只观测一次。
观测是模型输出，不是 T 收；T 收盘后剩余为 0，滤波结束。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_NOWCAST: Dict[str, Any] = {
    "enabled": False,
    "write_shadow": False,
    "use_as_rank_key": False,
    "taus": ["eod", "open"],
    "q_process": 0.05,
    "prior_var": None,
    "theme_q_boost": 2.0,
    "gap_q_trigger_pct": 2.0,
    "gap_q_boost": 2.0,
}

_ALLOWED_TAUS = ("eod", "open", "09:45", "14:00")
_TAU_INDEX = {t: i for i, t in enumerate(_ALLOWED_TAUS)}
_Q_BOOST_CAP = 4.0


def remaining_at_tau(
    yhat: Optional[float],
    realized_pct: Optional[float],
) -> Optional[float]:
    """把「从原点到 T 收」的 ŷ 映成当前时刻的剩余收益。

    (1+ŷ/100)/(1+r/100)−1。无已实现时退回 ŷ（尚未走过该段）。
    """
    if yhat is None:
        return None
    try:
        ye = float(yhat)
    except (TypeError, ValueError):
        return None
    if realized_pct is None:
        return round(ye, 6)
    try:
        r = float(realized_pct)
    except (TypeError, ValueError):
        return round(ye, 6)
    denom = 1.0 + r / 100.0
    if abs(denom) < 1e-12:
        return None
    return round(((1.0 + ye / 100.0) / denom - 1.0) * 100.0, 6)


def remap_variance(p: float, realized_pct: Optional[float]) -> float:
    """几何映剩余窗时，方差按 Jacobian² 缩放：1/(1+r/100)²。"""
    p = _as_var(p, 1.0)
    if realized_pct is None:
        return p
    try:
        r = float(realized_pct)
    except (TypeError, ValueError):
        return p
    scale = 1.0 + r / 100.0
    if abs(scale) < 1e-12:
        return p
    return max(1e-12, p / (scale * scale))


def compound_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None and b is None:
        return None
    try:
        fa = 0.0 if a is None else float(a)
        fb = 0.0 if b is None else float(b)
    except (TypeError, ValueError):
        return a if b is None else b
    return ((1.0 + fa / 100.0) * (1.0 + fb / 100.0) - 1.0) * 100.0


def _as_var(v: Any, default: float = 1.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = float(default)
    return max(1e-6, x)


def _as_opt_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def as_process_q(v: Any, default: float = 0.05) -> float:
    """过程噪声 q。``0`` 是合法值，不能用 ``v or default``。"""
    if v is None:
        return max(0.0, float(default))
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return max(0.0, float(default))


def _theme_on(theme_day: Any) -> bool:
    try:
        return float(theme_day or 0.0) >= 0.5
    except (TypeError, ValueError):
        return False


def _boost_ge1(v: Any, default: float = 2.0) -> float:
    try:
        x = float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        x = float(default)
    return max(1.0, x)


def adaptive_process_q(
    q_base: Any = 0.05,
    *,
    theme_day: Any = None,
    gap_pct: Any = None,
    theme_q_boost: Any = 2.0,
    gap_q_trigger_pct: Any = 2.0,
    gap_q_boost: Any = 2.0,
) -> Tuple[float, str]:
    """主题日 / 大缺口放大 Q；q_base=0 时仍为 0。"""
    q0 = as_process_q(q_base, 0.05)
    if q0 <= 0.0:
        return 0.0, "q=0"
    q = q0
    notes: List[str] = []
    if _theme_on(theme_day):
        b = _boost_ge1(theme_q_boost, 2.0)
        q *= b
        notes.append(f"theme×{b:g}")
    try:
        g = abs(float(gap_pct or 0.0))
    except (TypeError, ValueError):
        g = 0.0
    try:
        trig = float(gap_q_trigger_pct) if gap_q_trigger_pct is not None else 2.0
    except (TypeError, ValueError):
        trig = 2.0
    trig = max(0.0, trig)
    if g >= trig and trig > 0.0:
        b = _boost_ge1(gap_q_boost, 2.0)
        q *= b
        notes.append(f"gap×{b:g}")
    cap = q0 * _Q_BOOST_CAP
    if q > cap:
        q = cap
        notes.append("cap")
    return q, ("+".join(notes) if notes else "base")


def normalize_tau_label(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return "open"
    if s in _ALLOWED_TAUS:
        return s
    low = s.lower()
    if low in ("0945", "09:45"):
        return "09:45"
    if low in ("1400", "14:00"):
        return "14:00"
    if low == "eod":
        return "eod"
    if low == "open":
        return "open"
    hm = None
    if "T" in s:
        rest = s.split("T", 1)[1]
        if len(rest) >= 5 and rest[2] == ":":
            hm = rest[:5]
    elif len(s) >= 5 and s[2] == ":":
        hm = s[:5]
    if not hm:
        return "open"
    if hm < "09:45":
        return "open"
    if hm < "14:00":
        return "09:45"
    if hm < "15:05":
        return "14:00"
    return "open"


def live_tau_path(configured: Optional[Sequence[str]], as_of: Any) -> List[str]:
    """已到达且已配置的时钟。分钟 τ 不叠：09:45 与 14:00 只取当前档。"""
    allowed = {str(t).strip() for t in (configured or []) if str(t).strip()}
    allowed.add("eod")
    now = normalize_tau_label(as_of)
    now_i = _TAU_INDEX.get(now, 1)
    out: List[str] = ["eod"]
    if "open" in allowed and _TAU_INDEX["open"] <= now_i:
        out.append("open")
    if now in ("09:45", "14:00") and now in allowed:
        out.append(now)
    return out


def merge_nowcast_cfg(raw: Any) -> Dict[str, Any]:
    out = dict(DEFAULT_NOWCAST)
    if isinstance(raw, dict):
        out.update(raw)
    out["enabled"] = bool(out.get("enabled", False))
    if isinstance(raw, dict) and "write_shadow" in raw:
        out["write_shadow"] = bool(raw.get("write_shadow"))
    else:
        out["write_shadow"] = bool(out["enabled"])
    out["use_as_rank_key"] = bool(out.get("use_as_rank_key", False))
    out["q_process"] = as_process_q(out.get("q_process"), 0.05)
    if out.get("prior_var") is not None:
        try:
            out["prior_var"] = _as_var(out.get("prior_var"), 1.0)
        except (TypeError, ValueError):
            out["prior_var"] = None
    out["theme_q_boost"] = _boost_ge1(out.get("theme_q_boost"), 2.0)
    out["gap_q_boost"] = _boost_ge1(out.get("gap_q_boost"), 2.0)
    try:
        trig = float(out.get("gap_q_trigger_pct"))
    except (TypeError, ValueError):
        trig = 2.0
    out["gap_q_trigger_pct"] = max(0.0, trig)
    cleaned: List[str] = []
    src = out.get("taus") or ["eod", "open"]
    if not isinstance(src, (list, tuple)):
        src = ["eod", "open"]
    for t in src:
        s = str(t or "").strip()
        if s == "0945":
            s = "09:45"
        if s == "1400":
            s = "14:00"
        if s in _ALLOWED_TAUS and s not in cleaned:
            cleaned.append(s)
    if "eod" not in cleaned:
        cleaned.insert(0, "eod")
    if not any(x != "eod" for x in cleaned):
        cleaned.append("open")
    out["taus"] = cleaned
    return out


def kalman_update(
    x: Optional[float],
    P: float,
    z: Optional[float],
    R: float,
) -> Dict[str, Any]:
    """一维 Kalman：观测 z 更新状态 x。缺观测则不更新。"""
    P = _as_var(P, 1.0)
    R = _as_var(R, 1.0)
    if z is None:
        return {
            "x": None if x is None else round(float(x), 6),
            "P": round(P, 6),
            "K": 0.0,
            "updated": False,
        }
    try:
        zf = float(z)
    except (TypeError, ValueError):
        return {
            "x": None if x is None else round(float(x), 6),
            "P": round(P, 6),
            "K": 0.0,
            "updated": False,
        }
    if x is None:
        return {"x": round(zf, 6), "P": round(R, 6), "K": 1.0, "updated": True}
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return {"x": round(zf, 6), "P": round(R, 6), "K": 1.0, "updated": True}
    denom = P + R
    K = P / denom if denom > 1e-12 else 1.0
    x_post = xf + K * (zf - xf)
    P_post = (1.0 - K) * P
    return {
        "x": round(x_post, 6),
        "P": round(max(1e-12, P_post), 6),
        "K": round(K, 6),
        "updated": True,
    }


def kalman_nowcast_seq(
    steps: Sequence[Dict[str, Any]],
    *,
    prior_var: float = 1.0,
) -> Dict[str, Any]:
    """顺序滤波。每步：映到新剩余窗 → 加 Q →（可选）用该步 ŷ 更新。

    步字段：tau, yhat（已在该 τ 剩余窗）, realized_from_prev, obs_var, q, observe。
    EOD 步只初始化，不观测。
    """
    revisions: List[Dict[str, Any]] = []
    P = _as_var(prior_var, 1.0)
    x: Optional[float] = None
    last_k: Optional[float] = None
    as_of_out: Optional[str] = None
    q_used = 0.0
    eod_yhat: Optional[float] = None
    realized_acc: Optional[float] = None
    any_update = False

    seq = [s for s in (steps or []) if isinstance(s, dict)]
    for i, raw in enumerate(seq):
        tau = normalize_tau_label(raw.get("tau") or ("eod" if i == 0 else "open"))
        yhat = _as_opt_float(raw.get("yhat"))
        if i == 0:
            x = yhat
            eod_yhat = yhat
            as_of_out = tau
            revisions.append(
                {
                    "tau": tau,
                    "yhat": None if yhat is None else round(yhat, 6),
                    "remapped": None if x is None else round(float(x), 6),
                    "K": None,
                    "x_prior": None,
                    "x_post": None if x is None else round(float(x), 6),
                    "P": round(P, 6),
                    "R": round(P, 6),
                    "q": 0.0,
                    "updated": False,
                }
            )
            continue

        realized = _as_opt_float(raw.get("realized_from_prev"))
        if realized is not None:
            realized_acc = (
                realized if realized_acc is None else compound_pct(realized_acc, realized)
            )
        x_before = x
        if x is not None:
            x = remaining_at_tau(x, realized)
            P = remap_variance(P, realized)
        q = as_process_q(raw.get("q"), 0.0)
        P = P + q
        q_used = q
        observe = raw.get("observe")
        if observe is None:
            observe = yhat is not None
        observe = bool(observe) and yhat is not None
        r_obs = raw.get("obs_var")
        if observe:
            upd = kalman_update(x, P, yhat, _as_var(r_obs, 1.0))
            last_k = upd.get("K")
            any_update = True
            revisions.append(
                {
                    "tau": tau,
                    "yhat": round(yhat, 6),
                    "remapped": round(yhat, 6),
                    "K": last_k,
                    "x_prior": None if x is None else round(float(x), 6),
                    "x_post": upd.get("x"),
                    "P": upd.get("P"),
                    "R": round(_as_var(r_obs, 1.0), 6),
                    "q": q,
                    "updated": True,
                }
            )
            x = upd.get("x")
            P = float(upd.get("P") or P)
            as_of_out = tau
        else:
            revisions.append(
                {
                    "tau": tau,
                    "yhat": None,
                    "remapped": None if x is None else round(float(x), 6),
                    "K": 0.0,
                    "x_prior": None if x_before is None else round(float(x_before), 6),
                    "x_post": None if x is None else round(float(x), 6),
                    "P": round(P, 6),
                    "R": None,
                    "q": q,
                    "updated": False,
                }
            )
            as_of_out = tau

    if not any_update:
        as_of_out = "eod"

    x_prior = remaining_at_tau(eod_yhat, realized_acc)
    return {
        "predicted_score_nowcast": None if x is None else round(float(x), 6),
        "nowcast_as_of": as_of_out,
        "nowcast_revisions": revisions,
        "nowcast_K": last_k,
        "nowcast_P": round(float(P), 6) if P is not None else None,
        "nowcast_q": q_used,
        "nowcast_x_prior": None if x_prior is None else round(float(x_prior), 6),
        "nowcast_path": [str(r.get("tau")) for r in revisions],
    }


def kalman_nowcast(
    *,
    y_eod: Optional[float],
    y_tau: Optional[float],
    realized_eod_to_now: Optional[float],
    prior_var: float = 1.0,
    obs_var: float = 1.0,
    q_process: float = 0.05,
    as_of: str = "open",
    realized_open_to_tau: Optional[float] = None,
) -> Dict[str, Any]:
    """两点入口：EOD 先验 + 一个 τ 观测。缺观测则保持 EOD。"""
    tau_label = normalize_tau_label(as_of)
    q = as_process_q(q_process, 0.05)
    z = _as_opt_float(y_tau)
    if z is not None and tau_label in ("09:45", "14:00"):
        z = remaining_at_tau(z, realized_open_to_tau)
    steps: List[Dict[str, Any]] = [
        {"tau": "eod", "yhat": y_eod, "observe": False, "q": 0.0}
    ]
    if tau_label != "eod":
        steps.append(
            {
                "tau": tau_label,
                "yhat": z,
                "realized_from_prev": realized_eod_to_now,
                "obs_var": obs_var,
                "observe": z is not None,
                "q": q,
            }
        )
    pack = kalman_nowcast_seq(steps, prior_var=prior_var)
    pack["nowcast_q"] = q
    return pack


def run_live_nowcast(
    *,
    y_eod: Optional[float],
    y_tau: Optional[float],
    gap_pct: Optional[float] = None,
    ret_open_to_tau: Optional[float] = None,
    as_of: Any = "open",
    taus: Optional[Sequence[str]] = None,
    prior_var: float = 1.0,
    rem_model_doc: Optional[dict] = None,
    q_process: Any = 0.05,
    theme_day: Any = None,
    nowcast_cfg: Optional[dict] = None,
    allow_minute: bool = False,
) -> Dict[str, Any]:
    """Live：按已到达时钟顺序滤波；同一 ŷ_τ 只在最后一档观测一次。

    ``y_tau`` 为 rem 原始输出；分钟档按 rem 标签决定是否再映到剩余窗。
    """
    cfg = merge_nowcast_cfg(nowcast_cfg)
    taus_use = ensure_asof_in_taus(
        taus if taus is not None else cfg.get("taus"),
        as_of,
        allow_minute=bool(allow_minute) or ret_open_to_tau is not None,
    )
    path = live_tau_path(taus_use, as_of)
    if path and path[-1] in ("09:45", "14:00") and ret_open_to_tau is None:
        path = path[:-1]
    q_eff, q_note = adaptive_process_q(
        q_process if q_process is not None else cfg.get("q_process"),
        theme_day=theme_day,
        gap_pct=gap_pct,
        theme_q_boost=cfg.get("theme_q_boost"),
        gap_q_trigger_pct=cfg.get("gap_q_trigger_pct"),
        gap_q_boost=cfg.get("gap_q_boost"),
    )
    last_obs = None
    for t in path:
        if t != "eod":
            last_obs = t
    z_raw = _as_opt_float(y_tau)
    steps: List[Dict[str, Any]] = []
    for t in path:
        if t == "eod":
            steps.append({"tau": "eod", "yhat": y_eod, "observe": False, "q": 0.0})
            continue
        if t == "open":
            realized = gap_pct
            z = z_raw
        else:
            realized = ret_open_to_tau
            z = align_rem_yhat_to_clock(
                z_raw,
                rem_model_doc=rem_model_doc,
                ret_open_to_tau=ret_open_to_tau,
                clock=t,
            )
        observe = t == last_obs and z is not None
        r_obs = rem_obs_var(rem_model_doc, tau=t, theme_day=theme_day) if observe else None
        steps.append(
            {
                "tau": t,
                "yhat": z if observe else None,
                "realized_from_prev": realized,
                "obs_var": r_obs,
                "observe": observe,
                "q": q_eff,
            }
        )
    pack = kalman_nowcast_seq(steps, prior_var=prior_var)
    pack["nowcast_q"] = q_eff
    pack["nowcast_q_note"] = q_note
    pack["nowcast_path"] = path
    pack["nowcast_rem_oc"] = rem_label_is_open_to_close(rem_model_doc)
    return pack


def kalman_fusion_weights(
    *,
    prior_var: float,
    obs_var: float,
    q_process: float = 0.05,
) -> Tuple[float, float, float, str]:
    """两点 Kalman 等价权：ŷ = (1−K)·x_eod_rem + K·ŷ_τ。

    返回 (w_eod, w_tau, K, note)。
    """
    ve = _as_var(prior_var, 1.0)
    vt = _as_var(obs_var, 1.0)
    q = as_process_q(q_process, 0.05)
    P = ve + q
    denom = P + vt
    k = P / denom if denom > 1e-12 else 1.0
    we = 1.0 - k
    wt = k
    note = f"kalman(K={k:.3f},ve={ve:g},vt={vt:g},q={q:g})"
    return we, wt, k, note


def nordhaus_revision_slope(
    priors: Sequence[float],
    posts: Sequence[float],
) -> Optional[float]:
    """修正对先验的 OLS 斜率。有效 nowcast 应接近 0。"""
    xs: List[float] = []
    ys: List[float] = []
    for a, b in zip(priors, posts):
        try:
            p0 = float(a)
            p1 = float(b)
        except (TypeError, ValueError):
            continue
        xs.append(p0)
        ys.append(p1 - p0)
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    varx = sum((x - mx) ** 2 for x in xs)
    if varx < 1e-12:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(cov / varx, 6)


def nordhaus_from_nowcast_rows(rows: Sequence[Dict[str, Any]]) -> Optional[float]:
    """截面：EOD 映到当前窗的先验 vs Kalman 后验。"""
    priors: List[float] = []
    posts: List[float] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        p0 = row.get("nowcast_x_prior")
        p1 = row.get("predicted_score_nowcast")
        if p0 is None or p1 is None:
            continue
        try:
            priors.append(float(p0))
            posts.append(float(p1))
        except (TypeError, ValueError):
            continue
    return nordhaus_revision_slope(priors, posts)


def rem_label_is_open_to_close(rem_model_doc: Optional[dict]) -> bool:
    """rem 头是否估 open→close。分钟 τ 模型估 τ→close 时返回 False。"""
    doc = rem_model_doc if isinstance(rem_model_doc, dict) else {}
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
    if not isinstance(rm, dict):
        rm = {}
    mode = str(rm.get("horizon_mode") or doc.get("horizon_mode") or "").strip().lower()
    if mode in ("tau_to_close", "remaining", "tau_rem"):
        return False
    if mode in ("open_to_close", "oc", "open"):
        return True
    ys = rm.get("y_spec") if isinstance(rm.get("y_spec"), dict) else {}
    if not ys:
        ys = doc.get("y_spec") if isinstance(doc.get("y_spec"), dict) else {}
    tau = str((ys or {}).get("tau") or "open").strip().lower()
    formula = str((ys or {}).get("formula") or "").lower()
    if "price[" in formula:
        return False
    if "open" in formula:
        return True
    return tau in ("", "open")


def align_rem_yhat_to_clock(
    y_tau: Optional[float],
    *,
    rem_model_doc: Optional[dict] = None,
    ret_open_to_tau: Optional[float] = None,
    clock: Any = "open",
) -> Optional[float]:
    """把 rem ŷ 映到当前时钟的剩余窗，与 ŷ_EOD_rem / y_spec_tau 对齐。

    OC 头在 09:45/14:00：用 open→τ 已实现做几何剩余映射。
    已是 τ→close 的模型：不再映射，避免双重扣减。
    """
    clock_n = normalize_tau_label(clock)
    z = _as_opt_float(y_tau)
    if z is None:
        return None
    if clock_n not in ("09:45", "14:00"):
        return round(z, 6)
    if not rem_label_is_open_to_close(rem_model_doc):
        return round(z, 6)
    return remaining_at_tau(z, ret_open_to_tau)


def ensure_asof_in_taus(
    taus: Optional[Sequence[str]],
    as_of: Any,
    *,
    allow_minute: bool = False,
) -> List[str]:
    """分钟 as_of 且允许时，把当前时钟并进 nowcast.taus（不叠 09:45+14:00）。"""
    cleaned: List[str] = []
    src = taus or ["eod", "open"]
    if not isinstance(src, (list, tuple)):
        src = ["eod", "open"]
    for t in src:
        s = str(t or "").strip()
        if s == "0945":
            s = "09:45"
        if s == "1400":
            s = "14:00"
        if s in _ALLOWED_TAUS and s not in cleaned:
            cleaned.append(s)
    if "eod" not in cleaned:
        cleaned.insert(0, "eod")
    if not any(x != "eod" for x in cleaned):
        cleaned.append("open")
    if not allow_minute:
        return cleaned
    clock = normalize_tau_label(as_of)
    if clock in ("09:45", "14:00") and clock not in cleaned:
        cleaned.append(clock)
    return cleaned


def rem_obs_var(
    rem_model_doc: Optional[dict],
    default: float = 1.0,
    *,
    tau: Optional[str] = None,
    theme_day: Any = None,
) -> float:
    """τ 头观测噪声：by_tau → by_theme（PIT 分层）→ 总体 residual_var。"""
    doc = rem_model_doc if isinstance(rem_model_doc, dict) else {}
    oos = doc.get("oos") if isinstance(doc.get("oos"), dict) else {}
    if not oos:
        rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else {}
        oos = rm.get("oos") if isinstance(rm.get("oos"), dict) else {}

    def _from_bucket(bucket: Any) -> Optional[float]:
        if not isinstance(bucket, dict) or bucket.get("residual_var") is None:
            return None
        return _as_var(bucket.get("residual_var"), default)

    tau_lab = normalize_tau_label(tau) if tau else None
    by_tau = oos.get("by_tau") if isinstance(oos.get("by_tau"), dict) else {}
    if tau_lab and tau_lab != "eod":
        got = _from_bucket(by_tau.get(tau_lab))
        if got is not None:
            return got

    by_theme = oos.get("by_theme") if isinstance(oos.get("by_theme"), dict) else {}
    theme_key = "theme" if _theme_on(theme_day) else "normal"
    if by_theme:
        got = _from_bucket(by_theme.get(theme_key))
        if got is not None:
            return got
        got = _from_bucket(by_theme.get("all"))
        if got is not None:
            return got

    if oos.get("residual_var") is not None:
        return _as_var(oos.get("residual_var"), default)
    return _as_var(default, 1.0)


_CLUSTER_VAR_CACHE: Dict[str, Any] = {"mtime": None, "var": None}


def cluster_holdout_residual_var(
    report: Optional[dict] = None,
) -> Optional[float]:
    """分组 holdout RMSE² → EOD 先验方差。只用已落盘前向指标，无当日误差。"""
    doc = report
    if not isinstance(doc, dict):
        try:
            import json
            import os

            from core.paths import CLUSTER_LAST_REPORT_PATH

            path = CLUSTER_LAST_REPORT_PATH
            if not os.path.isfile(path):
                return None
            mtime = os.path.getmtime(path)
            if (
                _CLUSTER_VAR_CACHE.get("mtime") == mtime
                and _CLUSTER_VAR_CACHE.get("var") is not None
            ):
                return _CLUSTER_VAR_CACHE.get("var")
            with open(path, encoding="utf-8") as f:
                doc = json.load(f) or {}
            _CLUSTER_VAR_CACHE["mtime"] = mtime
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in nowcast_kf.py", exc_info=True)
            return None
    if not isinstance(doc, dict):
        return None
    rmse = None
    for bag in (
        doc.get("k_selection"),
        doc.get("walk_forward"),
        doc.get("oos"),
        doc,
    ):
        if not isinstance(bag, dict):
            continue
        if bag.get("mean_holdout_rmse") is not None:
            try:
                rmse = float(bag.get("mean_holdout_rmse"))
                break
            except (TypeError, ValueError):
                continue
        if bag.get("rmse") is not None:
            try:
                rmse = float(bag.get("rmse"))
                break
            except (TypeError, ValueError):
                continue
    if rmse is None or rmse <= 0:
        return None
    var = round(float(rmse) * float(rmse), 6)
    var = max(1e-6, var)
    if report is None:
        _CLUSTER_VAR_CACHE["var"] = var
    return var


def resolve_eod_prior_var(
    *,
    cfg_var: Any = None,
    prior_var: Any = None,
    cluster_report: Optional[dict] = None,
    prefer_cluster: bool = True,
) -> Tuple[float, str]:
    """EOD 先验方差：nowcast.prior_var → 分组 holdout RMSE² → dual_score.eod_residual_var。"""
    if prior_var is not None:
        return _as_var(prior_var, 1.0), "prior_var"
    if prefer_cluster:
        cv = cluster_holdout_residual_var(cluster_report)
        if cv is not None:
            return float(cv), "cluster_holdout_rmse2"
    if cfg_var is not None:
        return _as_var(cfg_var, 1.0), "eod_residual_var"
    return 1.0, "default"

