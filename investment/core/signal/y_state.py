"""高维状态 Y(τ)：路径均值 / 不确定度 / 分段 / 双头分歧 / EOD 校验。

不覆盖 ``predicted_score``。选股入池仍看 ŷ_EOD；本模块只服务展示、校验过滤与调仓信任。
规范见会话方案：展示投影 · 校验 · 过滤 · 按时刻读分量。
"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, Optional, Sequence, Tuple

CHECK_OK = "ok"
CHECK_CONFLICT = "conflict"
CHECK_LOW_CONF = "low_conf"
CHECK_MISSING_TAU = "missing_tau"
CHECK_SINGLE_HEAD = "single_head"

DEFAULT_Y_STATE: Dict[str, Any] = {
    "enabled": True,
    "eps_sign": 0.05,
    "disagree_warn": 0.5,
    "sigma_warn": 1.5,
    # 买入硬过滤：conflict / missing_tau（single_head / low_conf 默认只降权）
    "filter_buys": True,
    "block_on": ["conflict", "missing_tau"],
    "defer_on": ["low_conf", "single_head"],
    "show_badge": True,
    # 目标仓位：weight *= eod_trust（不归一，多出的变现金）
    "scale_weights": True,
    # 执行信任：乘到目标仓位（可选）
    "trust": {
        "ok": 1.0,
        "low_conf": 0.5,
        "single_head": 0.75,
        "conflict": 0.0,
        "missing_tau": 0.0,
    },
    # 建簿：按 check 优先级排序（越小越优先）
    "book_check_rank": {
        "ok": 0,
        "low_conf": 1,
        "single_head": 2,
        "conflict": 3,
        "missing_tau": 4,
    },
}


def get_y_state_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    """从 dual_score.y_state 或顶层 y_state 合并配置。"""
    raw = dict(DEFAULT_Y_STATE)
    raw["trust"] = dict(DEFAULT_Y_STATE["trust"])
    raw["book_check_rank"] = dict(DEFAULT_Y_STATE["book_check_rank"])
    src: Dict[str, Any] = {}
    if isinstance(config, dict):
        nested = config.get("dual_score")
        if isinstance(nested, dict) and isinstance(nested.get("y_state"), dict):
            src = dict(nested["y_state"])
        elif isinstance(config.get("y_state"), dict):
            src = dict(config["y_state"])
    else:
        try:
            from core.signal.config import load_signal_config
            from core.signal.dual_score import get_dual_score_cfg

            dual = get_dual_score_cfg(load_signal_config())
            if isinstance(dual.get("y_state"), dict):
                src = dict(dual["y_state"])
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in y_state.py", exc_info=True)
            src = {}
    trust_p = src.pop("trust", None)
    rank_p = src.pop("book_check_rank", None)
    raw.update(src)
    if isinstance(trust_p, dict):
        raw["trust"].update({str(k): float(v) for k, v in trust_p.items() if v is not None})
    if isinstance(rank_p, dict):
        raw["book_check_rank"].update(
            {str(k): int(v) for k, v in rank_p.items() if v is not None}
        )
    raw["enabled"] = bool(raw.get("enabled", True))
    raw["filter_buys"] = bool(raw.get("filter_buys", True))
    raw["show_badge"] = bool(raw.get("show_badge", True))
    raw["scale_weights"] = bool(raw.get("scale_weights", True))
    try:
        raw["eps_sign"] = float(raw.get("eps_sign") if raw.get("eps_sign") is not None else 0.05)
    except (TypeError, ValueError):
        raw["eps_sign"] = 0.05
    try:
        raw["disagree_warn"] = float(
            raw.get("disagree_warn") if raw.get("disagree_warn") is not None else 0.5
        )
    except (TypeError, ValueError):
        raw["disagree_warn"] = 0.5
    try:
        raw["sigma_warn"] = float(
            raw.get("sigma_warn") if raw.get("sigma_warn") is not None else 1.5
        )
    except (TypeError, ValueError):
        raw["sigma_warn"] = 1.5
    block = raw.get("block_on")
    if isinstance(block, (list, tuple)):
        raw["block_on"] = [str(x) for x in block]
    else:
        raw["block_on"] = list(DEFAULT_Y_STATE["block_on"])
    defer = raw.get("defer_on")
    if isinstance(defer, (list, tuple)):
        raw["defer_on"] = [str(x) for x in defer]
    else:
        raw["defer_on"] = list(DEFAULT_Y_STATE["defer_on"])
    return raw


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _resolve_sigma(item: dict, config: Optional[dict] = None) -> Tuple[Optional[float], str]:
    """不确定度：nowcast_P → τ OOS residual_var → eod_residual_var。"""
    p = _f(item.get("nowcast_P"))
    if p is not None and p >= 0:
        # P 为方差
        if p > 0:
            return round(math.sqrt(p), 6), "nowcast_P"
        return 0.0, "nowcast_P"
    for k in ("nowcast_var", "y_sigma"):
        s = _f(item.get(k))
        if s is not None and s >= 0:
            if k == "nowcast_var" and s > 0:
                return round(math.sqrt(s), 6), k
            return round(s, 6), k
    # τ 模型落盘 OOS
    try:
        from core.research.tau_ridge import load_tau_model

        doc = load_tau_model() or {}
        oos = doc.get("oos") if isinstance(doc.get("oos"), dict) else {}
        rv = _f(oos.get("residual_var")) if isinstance(oos, dict) else None
        if rv is not None and rv > 0:
            return round(math.sqrt(rv), 6), "rem_residual_var"
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in y_state.py", exc_info=True)
        pass
    try:
        from core.signal.dual_score import get_dual_score_cfg

        dual = get_dual_score_cfg(config)
        ve = _f(dual.get("eod_residual_var"))
        if ve is not None and ve > 0:
            return round(math.sqrt(ve), 6), "eod_residual_var"
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in y_state.py", exc_info=True)
        pass
    return None, "none"


def resolve_eod_check(
    *,
    eod_rem: Optional[float],
    y_tau: Optional[float],
    head: Optional[str],
    disagree: Optional[float],
    sigma: Optional[float],
    window: Optional[str],
    cfg: Optional[dict] = None,
) -> str:
    """EOD 当日执行校验枚举。"""
    ycfg = cfg if isinstance(cfg, dict) else get_y_state_cfg()
    eps = float(ycfg.get("eps_sign") or 0.05)
    d_warn = float(ycfg.get("disagree_warn") or 0.5)
    s_warn = float(ycfg.get("sigma_warn") or 1.5)
    win = str(window or "")
    # 收盘后窗：当日 τ 已实现，不做 missing/conflict 硬校
    if win == "eod_next":
        if sigma is not None and sigma >= s_warn:
            return CHECK_LOW_CONF
        return CHECK_OK
    head_s = str(head or "")
    if y_tau is None:
        return CHECK_MISSING_TAU
    if head_s.startswith("single"):
        return CHECK_SINGLE_HEAD
    if eod_rem is not None and y_tau is not None:
        if abs(eod_rem) >= eps and abs(y_tau) >= eps and (eod_rem > 0) != (y_tau > 0):
            return CHECK_CONFLICT
    if disagree is not None and disagree >= d_warn:
        return CHECK_LOW_CONF
    if sigma is not None and sigma >= s_warn:
        return CHECK_LOW_CONF
    return CHECK_OK


def eod_execution_trust(
    check: Optional[str],
    *,
    config: Optional[dict] = None,
    ycfg: Optional[dict] = None,
) -> float:
    cfg = ycfg if isinstance(ycfg, dict) else get_y_state_cfg(config)
    trust = cfg.get("trust") or {}
    key = str(check or CHECK_OK)
    try:
        return float(trust.get(key, 1.0 if key == CHECK_OK else 0.0))
    except (TypeError, ValueError):
        return 0.0


def scale_weights_by_eod_trust(
    weights: Dict[str, float],
    trust_by_code: Dict[str, float],
    *,
    config: Optional[dict] = None,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """目标仓位 × eod_trust；不重新归一到 100%（与 score_budget 留现金一致）。"""
    ycfg = get_y_state_cfg(config)
    meta: Dict[str, Any] = {
        "applied": False,
        "enabled": bool(ycfg.get("enabled", True)),
        "scale_weights": bool(ycfg.get("scale_weights", True)),
        "n_scaled": 0,
        "n_dropped": 0,
        "total_before": None,
        "total_after": None,
    }
    if not weights:
        return {}, meta
    before = {str(k): float(v) for k, v in weights.items() if v is not None}
    meta["total_before"] = round(sum(before.values()), 4)
    if not ycfg.get("enabled", True) or not ycfg.get("scale_weights", True):
        return dict(before), meta
    out: Dict[str, float] = {}
    for code, w in before.items():
        t_raw = trust_by_code.get(code)
        try:
            t = 1.0 if t_raw is None else float(t_raw)
        except (TypeError, ValueError):
            t = 1.0
        if not math.isfinite(t):
            t = 1.0
        t = max(0.0, min(1.0, t))
        nw = round(float(w) * t, 4)
        if t < 0.999:
            meta["n_scaled"] = int(meta["n_scaled"]) + 1
        if nw <= 0.05:
            if float(w) > 0.05:
                meta["n_dropped"] = int(meta["n_dropped"]) + 1
            continue
        out[code] = nw
    meta["applied"] = True
    meta["total_after"] = round(sum(out.values()), 4)
    return out, meta


def resolve_tau_to_close_segment(
    *,
    y_tau: Optional[float],
    eod_rem: Optional[float],
    ret_open_to_tau: Optional[float],
    rem_is_open_to_close: Optional[bool] = None,
    feats: Optional[dict] = None,
) -> Tuple[Optional[float], str]:
    """路径段 τ→收：优先显式特征，否则用 ŷ_τ 标签做代理。"""
    ft = feats if isinstance(feats, dict) else {}
    explicit = _f(ft.get("ret_tau_to_close"))
    if explicit is not None:
        return round(explicit, 6), "feature"
    # rem 已是 τ→close 标签
    if rem_is_open_to_close is False and y_tau is not None:
        return round(float(y_tau), 6), "tau_rem"
    # rem = open→close：用几何把已实现 open→τ 扣掉，得到期望 τ→close
    if y_tau is not None and ret_open_to_tau is not None:
        try:
            oc = float(y_tau)
            ot = float(ret_open_to_tau)
            denom = 1.0 + ot / 100.0
            if abs(denom) < 1e-9:
                return round(float(y_tau), 6), "tau_oc_proxy"
            tc = ((1.0 + oc / 100.0) / denom - 1.0) * 100.0
            return round(tc, 6), "tau_oc_adj"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if y_tau is not None:
        # 开盘 τ 或无分钟：ŷ_τ≈ open→close ≈ τ→close
        src = "tau_oc_proxy" if rem_is_open_to_close is not False else "tau_rem"
        return round(float(y_tau), 6), src
    if eod_rem is not None:
        return round(float(eod_rem), 6), "eod_rem_proxy"
    return None, "none"


def buy_passes_y_check(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Tuple[bool, Optional[str]]:
    """高维校验买入闸。返回 (ok, skip_reason)。``filter_buys=false`` 时恒过。"""
    ycfg = get_y_state_cfg(config)
    if not ycfg.get("enabled", True) or not ycfg.get("filter_buys", True):
        return True, None
    if str((item or {}).get("dual_score_window") or "") == "eod_next":
        return True, None
    check = str((item or {}).get("y_check") or "")
    if not check:
        # 惰性装配
        st = build_y_state(item, config=config)
        check = str(st.get("check") or CHECK_OK)
        if isinstance(item, dict):
            attach_y_state_fields(item, st)
    block_on = set(ycfg.get("block_on") or [])
    if check in block_on:
        return False, f"Y 校验未过（{check}）· 降低对今日 EOD 执行信任"
    return True, None


def book_check_sort_key(item: Optional[dict], *, config: Optional[dict] = None) -> int:
    ycfg = get_y_state_cfg(config)
    ranks = ycfg.get("book_check_rank") or {}
    check = str((item or {}).get("y_check") or CHECK_OK)
    try:
        return int(ranks.get(check, 9))
    except (TypeError, ValueError):
        return 9


def build_y_state(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """从已挂双层字段的 item 装配 Y(τ)。"""
    ycfg = get_y_state_cfg(config)
    it = item if isinstance(item, dict) else {}
    from core.signal.dual_score import (
        rank_key_for_item,
        resolve_predicted_score_eod,
        resolve_predicted_score_eod_rem,
        resolve_predicted_score_tau,
    )

    eod = resolve_predicted_score_eod(it)
    eod_rem = resolve_predicted_score_eod_rem(it)
    if eod_rem is None:
        eod_rem = eod
    y_tau = resolve_predicted_score_tau(it)
    trade = _f(it.get("predicted_score_blend"))
    if trade is None:
        trade = _f(rank_key_for_item(it, config=config))
    head = it.get("dual_score_head")
    if not head and it.get("dual_score_single_head"):
        head = "single"
    window = it.get("dual_score_window")
    as_of_tau = as_of or it.get("as_of_tau") or it.get("rem_tau") or window

    disagree = None
    if eod_rem is not None and y_tau is not None:
        disagree = round(abs(float(eod_rem) - float(y_tau)), 6)
    sign_conflict = False
    eps = float(ycfg.get("eps_sign") or 0.05)
    if eod_rem is not None and y_tau is not None:
        if abs(eod_rem) >= eps and abs(y_tau) >= eps and (eod_rem > 0) != (y_tau > 0):
            sign_conflict = True

    feats = it.get("features_tau") if isinstance(it.get("features_tau"), dict) else {}
    gap = _f(it.get("gap_pct"))
    if gap is None:
        gap = _f(feats.get("gap_pct"))
    ret_ot = _f(feats.get("ret_open_to_tau"))
    if ret_ot is None:
        ret_ot = _f(it.get("ret_open_to_tau"))
    sigma, sigma_src = _resolve_sigma(it, config)

    rem_oc: Optional[bool] = None
    ft_terms = it.get("formula_terms_tau") if isinstance(it.get("formula_terms_tau"), dict) else {}
    if "rem_oc" in ft_terms:
        rem_oc = bool(ft_terms.get("rem_oc"))
    elif it.get("rem_oc") is not None:
        rem_oc = bool(it.get("rem_oc"))
    else:
        try:
            from core.research.tau_ridge import load_tau_model
            from core.signal.nowcast_kf import rem_label_is_open_to_close

            rem_oc = rem_label_is_open_to_close(load_tau_model())
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in y_state.py", exc_info=True)
            rem_oc = None

    # 路径均值：优先 nowcast（若启用为排序或已有值且配置 prefer），默认 trade
    mu = trade
    nc = _f(it.get("predicted_score_nowcast"))
    try:
        from core.signal.dual_score import get_dual_score_cfg

        dual = get_dual_score_cfg(config)
        ncfg = dual.get("nowcast") if isinstance(dual.get("nowcast"), dict) else {}
        if ncfg.get("use_as_rank_key") and nc is not None:
            mu = nc
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in y_state.py", exc_info=True)
        pass

    check = resolve_eod_check(
        eod_rem=eod_rem,
        y_tau=y_tau,
        head=str(head) if head else None,
        disagree=disagree,
        sigma=sigma,
        window=str(window) if window else None,
        cfg=ycfg,
    )
    trust = eod_execution_trust(check, ycfg=ycfg)

    tau_to_close, tau_to_close_src = resolve_tau_to_close_segment(
        y_tau=y_tau,
        eod_rem=eod_rem,
        ret_open_to_tau=ret_ot,
        rem_is_open_to_close=rem_oc,
        feats=feats,
    )

    segments = {
        "gap": gap,
        "open_to_tau": ret_ot,
        "tau_to_close": tau_to_close,
        "tau_to_close_src": tau_to_close_src,
        "eod_close_close": eod,
        "tau_oc_proxy": y_tau,
    }

    return {
        "enabled": bool(ycfg.get("enabled", True)),
        "mu_path": round(mu, 6) if mu is not None else None,
        "sigma_path": sigma,
        "sigma_src": sigma_src,
        "segments": segments,
        "heads": {
            "eod": eod,
            "eod_rem": eod_rem,
            "tau": y_tau,
            "trade": trade,
            "nowcast": nc,
            "head": head,
        },
        "disagree": disagree,
        "sign_conflict": sign_conflict,
        "check": check,
        "eod_trust": trust,
        "as_of_tau": as_of_tau,
        "window": window,
        "show_badge": bool(ycfg.get("show_badge", True)),
    }


def attach_y_state_fields(item: dict, state: Optional[dict] = None, *, config: Optional[dict] = None) -> dict:
    """就地写入扁平字段 + ``y_state`` 包，供 API/UI。"""
    st = state if isinstance(state, dict) else build_y_state(item, config=config)
    item["y_state"] = st
    item["y_mu"] = st.get("mu_path")
    item["y_sigma"] = st.get("sigma_path")
    item["y_disagree"] = st.get("disagree")
    item["y_check"] = st.get("check")
    item["y_sign_conflict"] = bool(st.get("sign_conflict"))
    item["eod_trust"] = st.get("eod_trust")
    segs = st.get("segments") if isinstance(st.get("segments"), dict) else {}
    item["y_tau_to_close"] = segs.get("tau_to_close")
    item["y_tau_to_close_src"] = segs.get("tau_to_close_src")
    return item


def stamp_y_state(item: Optional[dict], *, config: Optional[dict] = None) -> Optional[dict]:
    if not isinstance(item, dict):
        return item
    ycfg = get_y_state_cfg(config)
    if not ycfg.get("enabled", True):
        return item
    return attach_y_state_fields(item, config=config)


def y_state_book_fields(item: Optional[dict]) -> Dict[str, Any]:
    """供 dual_score_book_fields / insights 透传。"""
    if not isinstance(item, dict):
        return {}
    if item.get("y_check") is None:
        stamp_y_state(item)
    return {
        "y_mu": item.get("y_mu"),
        "y_sigma": item.get("y_sigma"),
        "y_disagree": item.get("y_disagree"),
        "y_check": item.get("y_check"),
        "y_sign_conflict": bool(item.get("y_sign_conflict")),
        "eod_trust": item.get("eod_trust"),
        "y_tau_to_close": item.get("y_tau_to_close"),
        "y_tau_to_close_src": item.get("y_tau_to_close_src"),
        "y_state": item.get("y_state"),
    }


def summarize_y_checks(items: Sequence[Optional[dict]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    n = 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        c = str(it.get("y_check") or "")
        if not c:
            # 惰性：账本旧行可能只有 eod/τ
            try:
                st = build_y_state(it)
                c = str(st.get("check") or "")
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in y_state.py", exc_info=True)
                c = ""
        if not c:
            continue
        n += 1
        counts[c] = counts.get(c, 0) + 1
    by_rows = [
        {
            "check": ck,
            "n": cnt,
            "label": check_label_zh(ck),
            "share": round(cnt / n, 4) if n else None,
        }
        for ck, cnt in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return {"n": n, "by_check": counts, "rows": by_rows}


def ledger_y_check_daily_summary(
    as_of: Optional[str] = None,
    *,
    include_hit: bool = True,
) -> Dict[str, Any]:
    """日报用：最近（或指定）账本日的 Y 校验分桶；可选挂复盘命中。"""
    out: Dict[str, Any] = {
        "success": False,
        "as_of": None,
        "n": 0,
        "by_check": {},
        "rows": [],
        "by_y_check": [],
        "note": None,
    }
    try:
        from core.score_ledger import build_score_review, list_ledger_dates, load_ledger
    except Exception as exc:
        logger.exception('unexpected error in ledger_y_check_daily_summary')
        out["note"] = f"账本不可用: {exc}"
        return out
    d = str(as_of or "").strip() or None
    if not d:
        dates = list_ledger_dates(limit=5) or []
        d = str(dates[0]).strip() if dates else None
    if not d:
        out["note"] = "无 score ledger 日"
        return out
    out["as_of"] = d
    try:
        ledger = load_ledger(d) or {}
        rows = list(ledger.get("rows") or [])
    except Exception as exc:
        logger.exception('unexpected error in ledger_y_check_daily_summary')
        out["note"] = f"读账本失败: {exc}"
        return out
    base = summarize_y_checks(rows)
    out["n"] = int(base.get("n") or 0)
    out["by_check"] = dict(base.get("by_check") or {})
    out["rows"] = list(base.get("rows") or [])
    out["success"] = True
    if include_hit and out["n"] > 0:
        try:
            rev = build_score_review(d, horizon_days=1, autofill=False) or {}
            by = (rev.get("summary") or {}).get("by_y_check")
            if isinstance(by, list) and by:
                out["by_y_check"] = by
                out["hit_rate"] = (rev.get("summary") or {}).get("hit_rate")
                out["n_scored"] = (rev.get("summary") or {}).get("n_scored")
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in y_state.py", exc_info=True)
            pass
    if not out.get("by_y_check"):
        out["by_y_check"] = [
            {
                "check": r.get("check"),
                "n": r.get("n"),
                "hits": None,
                "wrong": None,
                "hit_rate": None,
                "label": r.get("label"),
                "share": r.get("share"),
            }
            for r in out["rows"]
        ]
    mix = " · ".join(
        f"{check_label_zh(r.get('check'))} {r.get('n')}"
        for r in (out.get("by_y_check") or out.get("rows") or [])[:5]
        if r.get("n")
    )
    out["summary_line"] = (
        f"Y校验 {d}：n={out['n']}" + (f" · {mix}" if mix else "")
    )
    return out


CHECK_LABEL_ZH = {
    CHECK_OK: "校验通过",
    CHECK_CONFLICT: "双头分歧",
    CHECK_LOW_CONF: "低置信",
    CHECK_MISSING_TAU: "缺 τ",
    CHECK_SINGLE_HEAD: "单头",
}


def check_label_zh(check: Optional[str]) -> str:
    return CHECK_LABEL_ZH.get(str(check or ""), str(check or "—"))
