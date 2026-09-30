"""四段 ŷ 窗口：抽头、加权融合、调仓 ranking、做 T residual。

ŷ_oo  open(T)→open(T+1)   主字段 y_oo / predicted_score_oo；别名 predicted_score
ŷ_τc  price(τ)→close(T)   主字段 y_τc。同一预估的别名 y_oc / y_tau（τ=open 时等于 close/open−1）
ŷ_co  close(T)→open(T+1)  主字段 y_co；旧键 y_on 可读

现网 ranking（τc，price(τ)→close 标签；行上有 y_spec_tau）：
  rank = w_oo·((ŷ_oo+1)/(1+rot)−1) + w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1)
  rot = price(τ)/open[T]−1；两项都在 τ→open[T+1]，不再整段减 rot
  缺 y_spec 的旧行仍走 open 基准融合再减 rot

R̂_τ      = close[T]/price(τ)−1
residual = fuse(ŷ_τc, remaining(ŷ_oc))   # 同空间；ĉ=price(τ)×(1+R̂_τ/100)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

_PCT_ABS_MAX = 20.0

FORMULA_OO = "open[T+1]/open[T]-1"
FORMULA_OC = "close[T]/open[T]-1"
FORMULA_CO = "open[T+1]/close[T]-1"
FORMULA_TC = "close[T]/price[τ]-1"
FORMULA_TC_LEGACY = "price[τ]/close[T]-1"
FORMULA_TC_REMAINING = "remaining(ŷ_oc)=(1+ŷ_oc)/(1+open→τ)−1"
FORMULA_RANKING = (
    "w_oo·((ŷ_oo+1)/(1+rot)−1) + w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1)"
)
FORMULA_RESIDUAL = "w_τc·ŷ_τc + w_oc·remaining(ŷ_oc)"
Y_TC_SOURCE_REMAINING = "remaining_oc"
Y_TC_SOURCE_RIDGE = "ridge"


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _pct_ok(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    if abs(float(v)) > _PCT_ABS_MAX:
        return None
    return float(v)


def fuse_pct(
    left: Optional[float],
    right: Optional[float],
    *,
    w_left: float = 0.5,
    w_right: float = 0.5,
) -> Optional[float]:
    """百分点加权融合。缺一侧用另一侧；都缺则 None。"""
    a = _f(left)
    b = _f(right)
    if a is None and b is None:
        return None
    if a is None:
        return float(b)
    if b is None:
        return float(a)
    wl = max(0.0, float(w_left))
    wr = max(0.0, float(w_right))
    s = wl + wr
    if s <= 1e-12:
        return 0.5 * float(a) + 0.5 * float(b)
    return (wl * float(a) + wr * float(b)) / s


def pc_formula_is_legacy(formula: Optional[str]) -> bool:
    """旧 ŷ_τc 标签 price(τ)/close−1（price 在分子）。"""
    s = str(formula or "").replace(" ", "").replace("（", "(").replace("）", ")")
    if not s:
        return False
    low = s.lower()
    if "price" not in low or "close" not in low:
        return False
    return low.find("price") < low.find("close")


def _pc_formula_of(item: Optional[dict]) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for key in ("y_spec_τc", "y_spec_tc", "y_spec_to", "y_spec_pc", "y_spec_r"):
        spec = item.get(key)
        if isinstance(spec, dict):
            f = spec.get("formula")
            if f:
                return str(f)
        elif spec:
            return str(spec)
    spec = item.get("y_spec")
    if isinstance(spec, dict):
        f = str(spec.get("formula") or "")
        low = f.lower()
        if "price" in low and "close" in low:
            return f
    elif spec:
        s = str(spec)
        low = s.lower()
        if "price" in low and "close" in low:
            return s
    rm = item.get("return_model")
    if isinstance(rm, dict):
        nested = _pc_formula_of(rm)
        if nested:
            return nested
    target = str(item.get("horizon_mode") or item.get("target") or "").replace("-", "_").lower()
    if "close_over_price" in target:
        return FORMULA_TC
    if "price_over_close" in target:
        return FORMULA_TC_LEGACY
    return None


pc_formula_of = _pc_formula_of


def invert_price_over_close(y_r: Optional[float]) -> Optional[float]:
    """price(τ)/close−1 → close/price(τ)−1（百分点）。"""
    r = _f(y_r)
    if r is None:
        return None
    d = 1.0 + float(r) / 100.0
    if abs(d) < 1e-12:
        return None
    return (1.0 / d - 1.0) * 100.0


def _first_pct(item: Optional[dict], keys: Tuple[str, ...]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    for k in keys:
        v = _pct_ok(_f(item.get(k)))
        if v is not None:
            return v
    return None


def pick_y_oo(item: Optional[dict]) -> Optional[float]:
    return _first_pct(
        item,
        (
            "y_oo",
            "predicted_score_oo",
            "predicted_score",
            "y_eod",
            "predicted_score_eod",
        ),
    )


def pick_y_oc(item: Optional[dict]) -> Optional[float]:
    return _first_pct(
        item,
        ("y_oc", "predicted_score_oc", "y_tau", "y_tau_oc", "predicted_score_tau"),
    )


def pick_y_τc(item: Optional[dict]) -> Optional[float]:
    """主字段 y_τc；旧簿可读 y_tc / y_to / y_pc；再缺则把旧 y_r（price/close）反几何。"""
    direct = _first_pct(
        item,
        (
            "y_τc",
            "predicted_score_τc",
            "y_tc",
            "predicted_score_tc",
            "y_to",
            "predicted_score_to",
            "y_pc",
            "predicted_score_pc",
        ),
    )
    if direct is not None:
        return direct
    raw = _first_pct(item, ("y_r", "y_r_hat", "predicted_score_r"))
    if raw is None:
        return None
    formula = _pc_formula_of(item)
    if formula and not pc_formula_is_legacy(formula):
        return raw
    return invert_price_over_close(raw)


def pick_y_tc(item: Optional[dict]) -> Optional[float]:
    """兼容旧名：pick_y_τc。"""
    return pick_y_τc(item)


def write_y_τc(dest: Dict[str, Any], val: Optional[float]) -> None:
    """只写主字段 y_τc / predicted_score_τc。"""
    if not isinstance(dest, dict) or val is None:
        return
    x = float(val)
    dest["y_τc"] = x
    dest["predicted_score_τc"] = x


def write_y_tc(dest: Dict[str, Any], val: Optional[float]) -> None:
    """兼容旧名：write_y_τc。"""
    write_y_τc(dest, val)


_Y_TC_HAT_KEYS = (
    "y_τc",
    "predicted_score_τc",
    "y_tc",
    "predicted_score_r",
    "y_r_hat",
    "y_r",
)
_Y_TC_LABEL_KEYS = ("r_realized", "y_r_realized", "y_τc_realized")


def pick_y_tc_hat(*objs: Any) -> Optional[float]:
    """读已写下的 ŷ_τc；旧列 y_r 仍可读。"""
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in _Y_TC_HAT_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def pick_y_tc_label(*objs: Any) -> Optional[float]:
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in _Y_TC_LABEL_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def write_y_tc_hat(dest: Dict[str, Any], val: float, *, formula: Any = None, model_doc: Any = None) -> None:
    """把一个数写入 y_τc，并留旧列 y_r。旧公式 price/close 时反几何。"""
    x = float(val)
    dest["predicted_score_r"] = x
    dest["y_r_hat"] = x
    dest["y_r"] = x
    spec = formula
    if spec is None and isinstance(model_doc, dict):
        spec = pc_formula_of(model_doc)
        if not spec:
            rm = model_doc.get("return_model")
            if isinstance(rm, dict):
                spec = pc_formula_of(rm)
    if spec is None:
        spec = pc_formula_of(dest)
    if spec is None:
        spec = FORMULA_TC
    dest["y_spec_r"] = {"formula": str(spec), "unit": "pct"}
    if pc_formula_is_legacy(str(spec)):
        pc = invert_price_over_close(x)
        dest["y_spec_τc"] = {"formula": FORMULA_TC, "unit": "pct", "from": "legacy_invert"}
    else:
        pc = x
        dest["y_spec_τc"] = {"formula": str(spec), "unit": "pct"}
    write_y_τc(dest, pc)


def write_y_tc_label(dest: Dict[str, Any], val: float) -> None:
    x = float(val)
    dest["r_realized"] = x
    dest["y_r_realized"] = x


def pack_y_tc_fields(day: Optional[dict]) -> Dict[str, Any]:
    val = pick_y_tc_label(day) if isinstance(day, dict) else None
    return {"r_realized": val, "y_r_realized": val}


def tc_realized_pct(price_tau: Any, close: Any) -> Optional[float]:
    """close/price(τ)−1（百分点，四位）。"""
    from core.research.tau_panel import y_r_pct

    v = y_r_pct(price_tau, close)
    return round(float(v), 4) if v is not None else None


def stamp_remaining_y_τc(
    dest: Dict[str, Any],
    *,
    y_oc: Optional[float] = None,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
) -> Optional[float]:
    """remaining(ŷ_oc) 写入 R̂_τ（r_hat / remaining_oc）；ŷ_τc 保持 Ridge 模型预估。

    旧簿若把 remaining 盖进 y_τc，则从 y_τc_ridge / y_r 还原表列，避免与 R_τ 重复。
    """
    if not isinstance(dest, dict):
        return None
    oc = y_oc if y_oc is not None else pick_y_oc(dest)
    rem = remaining_oc(
        oc,
        ret_open_to_tau_of(dest),
        open_px=open_px,
        price_tau=price_tau,
    )
    ridge = _f(dest.get("y_τc_ridge"))
    if ridge is None:
        ridge = _f(dest.get("y_r"))
    if ridge is None:
        ridge = _f(dest.get("y_r_hat"))
    if ridge is None:
        ridge = _f(dest.get("predicted_score_r"))
    src = str(dest.get("y_τc_source") or "")
    if ridge is None and src != Y_TC_SOURCE_REMAINING:
        ridge = _f(dest.get("y_τc"))
    if rem is not None:
        dest["remaining_oc"] = float(rem)
        dest["r_hat"] = float(rem)
        dest["residual"] = float(rem)
    if ridge is not None:
        dest["y_τc_ridge"] = float(ridge)
        write_y_τc(dest, ridge)
        dest["y_τc_source"] = Y_TC_SOURCE_RIDGE
        dest["y_spec_τc"] = {"formula": FORMULA_TC, "unit": "pct"}
    return rem if rem is not None else ridge


def forward_oo_pct(
    bars: Sequence[dict],
    idx: int,
    horizon: int = 1,
) -> Optional[float]:
    """open[idx+h]/open[idx]−1（百分点）。ŷ_oo 训练/对账标签。"""
    if idx < 0 or horizon < 1:
        return None
    n = len(bars or ())
    if idx + horizon >= n:
        return None
    o0 = _f((bars[idx] or {}).get("open"))
    o1 = _f((bars[idx + horizon] or {}).get("open"))
    if o0 is None or o1 is None or o0 <= 0 or o1 <= 0:
        return None
    return (float(o1) / float(o0) - 1.0) * 100.0


def pit_oo_window_quote(
    bars: Sequence[dict],
    idx: int,
    start: int,
) -> Tuple[List[dict], Dict[str, Any]]:
    """ŷ_oo PIT：X 只用到 T−1 收；quote = T 开 / 昨收缺口。今收不进 X。"""
    hist = list(bars[max(0, int(start)) : max(0, int(idx))])
    quote: Dict[str, Any] = {"change_raw": 0.0, "price_raw": None}
    if 0 <= idx < len(bars):
        o = _f((bars[idx] or {}).get("open"))
        if o is not None and o > 0:
            quote["price_raw"] = float(o)
        if idx >= 1:
            prev_c = _f((bars[idx - 1] or {}).get("close"))
            if o is not None and prev_c is not None and prev_c > 0:
                quote["change_raw"] = round((float(o) / float(prev_c) - 1.0) * 100.0, 4)
    return hist, quote


def pick_y_co(item: Optional[dict]) -> Optional[float]:
    return _first_pct(item, ("y_co", "predicted_score_co", "y_on", "predicted_score_on"))


def ret_open_to_tau_of(item: Optional[dict]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    v = _f(item.get("ret_open_to_tau"))
    if v is not None:
        return v
    feats = item.get("features_tau")
    if isinstance(feats, dict):
        v = _f(feats.get("ret_open_to_tau"))
        if v is not None:
            return v
    return None


def tau_model_is_open_to_close(item: Optional[dict]) -> bool:
    """从打分 item 判断 τ 头标签是否 open→close。

    依据 y_spec_tau.formula / rem_y_spec：
      - 含 price[τ] 或 horizon_mode=tau_to_close → τc 模型（返回 False）
      - 含 open 或缺失 → OC 模型（返回 True，向后兼容）
    """
    from core.signal.yhat_geom import rem_label_is_open_to_close

    if not isinstance(item, dict):
        return True
    y_spec = item.get("y_spec_tau")
    if not isinstance(y_spec, dict):
        rem = item.get("rem_y_spec")
        if rem:
            y_spec = {"formula": str(rem)}
        else:
            y_spec = {}
    return rem_label_is_open_to_close({"y_spec": y_spec})


def compound_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """((1+a/100)(1+b/100)−1)×100。缺一侧返回另一侧。"""
    fa = _f(a)
    fb = _f(b)
    if fa is None and fb is None:
        return None
    if fa is None:
        return float(fb)
    if fb is None:
        return float(fa)
    return ((1.0 + fa / 100.0) * (1.0 + fb / 100.0) - 1.0) * 100.0


def oc_with_co(
    y_oc: Optional[float],
    y_co: Optional[float],
    w_co: float = 0.0,
) -> Optional[float]:
    """((1+ŷ_oc/100)(1+w_co·ŷ_co/100)−1)×100。无 ŷ_oc 则 None；w_co=0 或缺 ŷ_co 则 ŷ_oc。"""
    if y_oc is None:
        return None
    try:
        wc = max(0.0, float(w_co or 0.0))
    except (TypeError, ValueError):
        wc = 0.0
    if y_co is None or wc <= 1e-12:
        return float(y_oc)
    return compound_pct(y_oc, wc * float(y_co))


def ret_open_to_tau_pct(
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
) -> Optional[float]:
    """price(τ)/open[T]−1（百分点）。"""
    o = _f(open_px)
    p = _f(price_tau)
    if o is None or p is None or o <= 0 or p <= 0:
        return None
    return (float(p) / float(o) - 1.0) * 100.0


def ranking_open_px(item: Optional[dict]) -> Optional[float]:
    """今开：day_open / open_raw / open_t / open。"""
    if not isinstance(item, dict):
        return None
    for k in ("day_open", "open_raw", "open_t", "open"):
        v = _f(item.get(k))
        if v is not None and v > 0:
            return v
    return None


def ranking_price_tau(item: Optional[dict]) -> Optional[float]:
    """τ 价：rebalance_px / price_tau / 现价。"""
    if not isinstance(item, dict):
        return None
    for k in ("rebalance_px", "price_tau", "price_raw", "last_price", "mark_price", "price"):
        v = _f(item.get(k))
        if v is not None and v > 0:
            return v
    return None


def remaining_oc(
    y_oc: Optional[float],
    ret_open_to_tau: Optional[float] = None,
    *,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
) -> Optional[float]:
    """把 ŷ_oc（open→close）映成 R̂_τ = close[T]/price(τ)−1。

    无 open→τ 已实现则无法对齐剩余窗，返回 None（勿把 OC 原值与 ŷ_τc 混加）。
    """
    if y_oc is None:
        return None
    rot = _f(ret_open_to_tau)
    if rot is None:
        rot = ret_open_to_tau_pct(open_px, price_tau)
    if rot is None:
        return None
    try:
        from core.signal.yhat_geom import remaining_at_tau

        return remaining_at_tau(y_oc, rot)
    except Exception:  # noqa: BLE001
        return None


def residual_inv_var_weights(
    item: Optional[dict],
    ret_open_to_tau: Optional[float] = None,
) -> Optional[Tuple[float, float]]:
    """OOS 逆方差权：w ∝ 1/σ²。ŷ_oc 方差先按 Jacobian 映到剩余窗。缺一侧则 None。"""
    d = item if isinstance(item, dict) else {}
    oos = d.get("oos") if isinstance(d.get("oos"), dict) else {}

    def _var(*keys: str) -> Optional[float]:
        for src in (d, oos):
            if not isinstance(src, dict):
                continue
            for k in keys:
                v = _f(src.get(k))
                if v is not None and float(v) > 1e-12:
                    return float(v)
        return None

    v_tc = _var(
        "residual_var_τc",
        "residual_var_tc",
        "r_residual_var",
        "y_τc_residual_var",
    )
    v_oc = _var("residual_var_oc", "tau_residual_var", "residual_var")
    if v_tc is None or v_oc is None:
        return None
    try:
        from core.signal.yhat_geom import remap_variance

        v_oc_m = remap_variance(v_oc, ret_open_to_tau)
    except Exception:  # noqa: BLE001
        v_oc_m = v_oc
    v_oc_m = max(float(v_oc_m), 1e-12)
    w_tc = 1.0 / v_tc
    w_oc = 1.0 / v_oc_m
    s = w_tc + w_oc
    if s <= 1e-12:
        return None
    return w_tc / s, w_oc / s


def residual_w_mode_from_cfg(cfg: Optional[dict]) -> str:
    d = cfg if isinstance(cfg, dict) else {}
    raw = str(d.get("residual_w_mode") or d.get("residual_fusion_mode") or "fixed").strip().lower()
    if raw in ("inv_var", "inverse_var", "inverse_variance", "oos", "variance"):
        return "inv_var"
    return "fixed"


def ranking_pct(
    item: Optional[dict],
    *,
    w_oo: float = 0.5,
    w_oc: float = 0.5,
    w_co: float = 0.0,
    rem_model_doc: Optional[dict] = None,
    ret_open_to_tau: Optional[float] = None,
) -> Optional[float]:
    """调仓 ranking（百分点），现网 τc。

    行上 ``y_spec_tau`` 为 price(τ)→close 时：
        ranking = w_oo·((ŷ_oo+1)/(1+rot)−1) + w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1)
        直接算在 τ→open[T+1]；ŷ_oo 用 remaining_at_tau，ŷ_τc 已是 τ→close。
        旧行没有 y_τc 时回退 y_oc。

    缺 y_spec 时退回 open 基准融合，由 ``remaining_ranking_pct`` 再减 rot。
    ``rem_model_doc`` 优先于 item。
    """
    from core.signal.yhat_geom import remaining_at_tau

    is_oc = tau_model_is_open_to_close(rem_model_doc if rem_model_doc is not None else item)
    y_co = pick_y_co(item)
    if is_oc:
        right = oc_with_co(pick_y_oc(item), y_co, w_co)
        return fuse_pct(pick_y_oo(item), right, w_left=w_oo, w_right=w_oc)
    y_tc = pick_y_τc(item)
    if y_tc is None:
        y_tc = pick_y_oc(item)
    rot = _f(ret_open_to_tau)
    if rot is None:
        rot = ret_open_to_tau_of(item)
    y_oo_rem = remaining_at_tau(pick_y_oo(item), rot)
    right = oc_with_co(y_tc, y_co, w_co)
    return fuse_pct(y_oo_rem, right, w_left=w_oo, w_right=w_oc)


def remaining_ranking_pct(
    ranking: Optional[float],
    ret_open_to_tau: Optional[float] = None,
    *,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
    is_oc_model: bool = True,
) -> Optional[float]:
    """决策 ranking（百分点）。

    τc：``ranking_pct`` 已在 τ→open[T+1]，直接透传。
    缺 y_spec 的旧行：融合分再减 (price(τ)/open−1)；缺开盘或 τ 价则原值。
    """
    if ranking is None:
        return None
    if not is_oc_model:
        return float(ranking)
    rot = _f(ret_open_to_tau)
    if rot is None:
        rot = ret_open_to_tau_pct(open_px, price_tau)
    if rot is None:
        return float(ranking)
    return round(float(ranking) - float(rot), 6)


def realized_ranking_pct(
    realized_oo: Optional[float],
    *,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
) -> Optional[float]:
    """ranking 真实（百分点）= realized_oo − (price(τ)/open−1)。

    即 (open[T+1] − price(τ)) / open[T]。缺次日开则 None；缺开盘或 τ 价时
    与预测一样不扣（真实=realized_oo）。
    """
    y = _f(realized_oo)
    if y is None:
        return None
    rot = ret_open_to_tau_pct(open_px, price_tau)
    if rot is None:
        return round(float(y), 6)
    return round(float(y) - float(rot), 6)


def residual_pct(
    item: Optional[dict],
    *,
    w_pc: float = 0.5,
    w_oc: float = 0.5,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
    cfg: Optional[dict] = None,
    use_τc: Optional[bool] = None,
) -> Optional[float]:
    """R̂_τ（百分点）= fuse(ŷ_τc, remaining(ŷ_oc))，标签 close[T]/price(τ)−1。

    ŷ_oc 是 open→close；融合前映到剩余窗。映不成则只用 ŷ_τc。
    缺 ŷ_τc 时 fuse 退回 remaining(ŷ_oc)；做 T mag 请用 ``t0_residual_pct``。
    """
    yhat = pick_y_τc(item) if use_τc is not False else None
    rot = ret_open_to_tau_pct(open_px, price_tau)
    if rot is None:
        rot = ret_open_to_tau_of(item)
    rem = remaining_oc(
        pick_y_oc(item),
        rot,
        open_px=open_px,
        price_tau=price_tau,
    )
    wl, wr = float(w_pc), float(w_oc)
    if cfg is not None:
        wl, wr = residual_weights_from_cfg(cfg)
        if residual_w_mode_from_cfg(cfg) == "inv_var":
            iv = residual_inv_var_weights(item, rot)
            if iv is not None:
                wl, wr = iv
    return fuse_pct(yhat, rem, w_left=wl, w_right=wr)


def t0_residual_pct(
    item: Optional[dict],
    *,
    w_pc: float = 0.5,
    w_oc: float = 0.5,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
    cfg: Optional[dict] = None,
) -> Optional[float]:
    """做 T 主分：有 ŷ_τc 才融合 ŷ_τc 与 remaining(ŷ_oc)；否则 None（回退 y_trade）。"""
    if pick_y_τc(item) is None:
        return None
    return residual_pct(
        item,
        w_pc=w_pc,
        w_oc=w_oc,
        open_px=open_px,
        price_tau=price_tau,
        cfg=cfg,
    )


def fusion_weights_from_cfg(cfg: Optional[dict]) -> Tuple[float, float]:
    """调仓权：fusion_w_oo / fusion_w_oc；旧键 fusion_w_trade / fusion_w_nowcast。"""
    d = cfg if isinstance(cfg, dict) else {}
    w_oo = _f(d.get("fusion_w_oo"))
    if w_oo is None:
        w_oo = _f(d.get("fusion_w_trade"))
    w_oc = _f(d.get("fusion_w_oc"))
    if w_oc is None:
        w_oc = _f(d.get("fusion_w_nowcast"))
    if w_oo is None:
        w_oo = 0.5
    if w_oc is None:
        w_oc = 0.5
    w_oo = max(0.0, min(1.0, float(w_oo)))
    w_oc = max(0.0, min(1.0, float(w_oc)))
    s = w_oo + w_oc
    if s <= 1e-12:
        return 0.5, 0.5
    return w_oo / s, w_oc / s


def fusion_w_co_from_cfg(cfg: Optional[dict]) -> float:
    """隔夜叠入 ŷ_oc 的系数；fusion_w_co / y_on_alpha；默认 0（不叠）。范围 0～10。"""
    d = cfg if isinstance(cfg, dict) else {}
    w = _f(d.get("fusion_w_co"))
    if w is None:
        w = _f(d.get("y_on_alpha"))
    if w is None:
        return 0.0
    return max(0.0, min(10.0, float(w)))


def residual_weights_from_cfg(cfg: Optional[dict]) -> Tuple[float, float]:
    d = cfg if isinstance(cfg, dict) else {}
    w = _f(d.get("fusion_w_τc"))
    if w is None:
        w = _f(d.get("fusion_w_tc"))
    if w is None:
        w = _f(d.get("fusion_w_to"))
    if w is None:
        w = _f(d.get("fusion_w_pc"))
    if w is None:
        w = _f(d.get("residual_w_pc"))
    w_oc = _f(d.get("residual_w_oc"))
    if w_oc is None:
        w_oc = _f(d.get("fusion_w_oc_r"))
    if w is None:
        w = 0.5
    if w_oc is None:
        w_oc = 0.5
    w = max(0.0, min(1.0, float(w)))
    w_oc = max(0.0, min(1.0, float(w_oc)))
    s = w + w_oc
    if s <= 1e-12:
        return 0.5, 0.5
    return w / s, w_oc / s


def stamp_window_scores(item: Optional[dict], cfg: Optional[dict] = None) -> Dict[str, Optional[float]]:
    """抽 ŷ_oo/ŷ_oc/ŷ_τc + ranking / residual（百分点）。"""
    w_oo, w_oc = fusion_weights_from_cfg(cfg)
    w_co = fusion_w_co_from_cfg(cfg)
    w_τc, w_oc_r = residual_weights_from_cfg(cfg)
    y_oo = pick_y_oo(item)
    y_oc = pick_y_oc(item)
    y_τc = pick_y_τc(item)
    y_co = pick_y_co(item)
    is_oc = tau_model_is_open_to_close(item)
    ranking = ranking_pct(item, w_oo=w_oo, w_oc=w_oc, w_co=w_co)
    ranking = remaining_ranking_pct(
        ranking,
        open_px=ranking_open_px(item),
        price_tau=ranking_price_tau(item),
        is_oc_model=is_oc,
    )
    residual = residual_pct(item, w_pc=w_τc, w_oc=w_oc_r, cfg=cfg)
    fa = item.get("factor_anomaly") if isinstance(item, dict) else None
    if isinstance(fa, dict) and fa.get("fatal_tau"):
        y_oc = None
        y_co = None
        ranking = None
    return {
        "y_oo": y_oo,
        "y_oc": y_oc,
        "y_τc": y_τc,
        "y_co": y_co,
        "ranking": ranking,
        "residual": residual,
        "r_hat": residual,
    }


__all__ = [
    "FORMULA_CO",
    "FORMULA_OC",
    "FORMULA_OO",
    "FORMULA_TC",
    "FORMULA_TC_LEGACY",
    "FORMULA_TC_REMAINING",
    "FORMULA_RANKING",
    "FORMULA_RESIDUAL",
    "Y_TC_SOURCE_REMAINING",
    "Y_TC_SOURCE_RIDGE",
    "compound_pct",
    "forward_oo_pct",
    "fuse_pct",
    "fusion_w_co_from_cfg",
    "fusion_weights_from_cfg",
    "oc_with_co",
    "invert_price_over_close",
    "pc_formula_is_legacy",
    "pc_formula_of",
    "pick_y_co",
    "pick_y_oc",
    "pick_y_oo",
    "pick_y_τc",
    "pick_y_tc",
    "pit_oo_window_quote",
    "ranking_open_px",
    "ranking_pct",
    "ranking_price_tau",
    "remaining_ranking_pct",
    "realized_ranking_pct",
    "remaining_oc",
    "residual_inv_var_weights",
    "residual_pct",
    "residual_w_mode_from_cfg",
    "residual_weights_from_cfg",
    "ret_open_to_tau_pct",
    "tau_model_is_open_to_close",
    "stamp_remaining_y_τc",
    "t0_residual_pct",
    "ret_open_to_tau_of",
    "stamp_window_scores",
    "write_y_τc",
    "write_y_tc",
]
