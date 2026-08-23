"""打分账本：昨日复盘 / 影子簿复盘报告。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from core.score_ledger import io as _lio
from core.numbers import date_key
from core.score_ledger.asof import (
    default_as_of,
)
from core.score_ledger.freeze import (
    load_nowcast_shadow_membership,
    load_tau_shadow_membership,
)
from core.score_ledger.outcomes import (
    _sign_hit,
    _spearman_ic,
    fill_outcomes,
    hydrate_ledger_yhat_tau,
)


def build_tau_shadow_review(
    as_of: Optional[str] = None,
    *,
    horizon_days: int = 1,
    autofill: bool = True,
) -> Dict[str, Any]:
    """A2 验收摘要：影子重叠 + IC(ŷ_τ, y_τ) + 方向命中。"""
    d = date_key(as_of) or default_as_of()
    h = max(1, min(int(horizon_days or 1), 10))
    try:
        hydrate_ledger_yhat_tau(d, persist=True)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        pass
    if autofill:
        try:
            fill_outcomes(d, horizon_days=h)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            pass
    ledger = _lio.load_ledger(d)
    outcomes = _lio.load_outcomes(d)
    shadow = load_tau_shadow_membership(d)
    by_oc = outcomes.get("by_code") or {}

    xs: List[float] = []
    ys: List[float] = []
    hits = 0
    hit_n = 0
    for r in ledger.get("rows") or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "").strip()
        yhat_tau = _lio._to_float(r.get("yhat_tau"))
        oc = by_oc.get(code) or {}
        y_tau = _lio._to_float(oc.get("realized_tau"))
        if yhat_tau is None:
            yhat_tau = _lio._to_float(oc.get("yhat_tau"))
        if yhat_tau is None or y_tau is None:
            continue
        xs.append(float(yhat_tau))
        ys.append(float(y_tau))
        hit = oc.get("sign_hit_tau")
        if hit is None:
            hit = _sign_hit(yhat_tau, y_tau)
        if hit is None:
            continue
        hit_n += 1
        if hit:
            hits += 1

    vs = (shadow.get("meta") or {}).get("vs_eod_book")
    if not isinstance(vs, dict):
        vs = None
        try:
            from core.signal.cluster_live import load_tau_shadow_cluster_book

            live_sh = load_tau_shadow_cluster_book() or {}
            vs = ((live_sh.get("meta") or {}).get("vs_eod_book"))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            vs = None

    ic = _spearman_ic(xs, ys)
    return {
        "success": True,
        "as_of": d,
        "horizon_days": h,
        "tau_ic_spearman": ic,
        "tau_n": len(xs),
        "tau_sign_hit_rate": (
            round(hits / hit_n, 4) if hit_n else None
        ),
        "tau_sign_hit_n": hit_n,
        "shadow_membership": {
            "exists": not bool(shadow.get("empty")),
            "n_rows": len(shadow.get("rows") or []),
            "path": shadow.get("path"),
            "vs_eod_book": vs,
        },
        "_lio.ledger_path": ledger.get("path"),
        "_lio.outcomes_path": outcomes.get("path"),
        "y_spec_tau": "close[T]/open[T]-1",
        "note": (
            "A2：IC/命中对 ŷ_τ↔y_τ；vs_eod 为影子簿与 EOD 簿成员重叠。"
            "不替代 EOD 复盘。"
        ),
    }


def _nowcast_vs_eod_from_ledger(
    ledger: Optional[dict],
    *,
    max_names: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """无影子快照时：用当日账本 Top-K(ŷ_nowcast) vs Top-K(ŷ) 估 Jaccard。"""
    if not isinstance(ledger, dict):
        return None
    rows = [r for r in (ledger.get("rows") or []) if isinstance(r, dict)]
    pool = [r for r in rows if r.get("in_book") is not False]
    if not pool:
        pool = rows
    if len(pool) < 2:
        return None
    meta = ledger.get("meta") if isinstance(ledger.get("meta"), dict) else {}
    n_book = 0
    try:
        n_book = int(meta.get("n_book") or 0)
    except (TypeError, ValueError):
        n_book = 0
    if n_book <= 0:
        n_book = sum(1 for r in pool if r.get("in_book"))
    cap = max_names or n_book or min(len(pool), 30)
    cap = max(1, int(cap))

    def _code(r: dict) -> str:
        return str(r.get("code") or r.get("stock_code") or "").strip()

    def _key(r: dict, *fields: str) -> float:
        for f in fields:
            v = _lio._to_float(r.get(f))
            if v is not None:
                return float(v)
        return float("-inf")

    eod_sorted = sorted(pool, key=lambda r: _key(r, "yhat", "yhat_eod"), reverse=True)
    nc_pool = [r for r in pool if _lio._to_float(r.get("yhat_nowcast")) is not None]
    if not nc_pool:
        return None
    nc_sorted = sorted(nc_pool, key=lambda r: _key(r, "yhat_nowcast"), reverse=True)
    try:
        from core.signal.dual_score import compare_book_overlap
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        return None
    eod_book = [{"stock_code": _code(r)} for r in eod_sorted[:cap] if _code(r)]
    nc_book = [{"stock_code": _code(r)} for r in nc_sorted[:cap] if _code(r)]
    if not eod_book or not nc_book:
        return None
    out = compare_book_overlap(eod_book, nc_book)
    out["source"] = "ledger_topk"
    return out


def build_nowcast_shadow_review(
    as_of: Optional[str] = None,
    *,
    horizon_days: int = 1,
    autofill: bool = True,
) -> Dict[str, Any]:
    """N3 验收摘要：影子重叠 + IC(ŷ_nowcast, 涨跌) + 方向命中 + Nordhaus。"""
    d = date_key(as_of) or default_as_of()
    h = max(1, min(int(horizon_days or 1), 10))
    try:
        hydrate_ledger_yhat_tau(d, persist=True)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        pass
    if autofill:
        try:
            fill_outcomes(d, horizon_days=h)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            pass
    ledger = _lio.load_ledger(d)
    outcomes = _lio.load_outcomes(d)
    shadow = load_nowcast_shadow_membership(d)
    by_oc = outcomes.get("by_code") or {}

    # 影子成员优先；无则用全账本有 yhat_nowcast 的行
    shadow_codes = {
        str(r.get("code") or "").strip()
        for r in (shadow.get("rows") or [])
        if isinstance(r, dict) and r.get("code")
    }
    xs: List[float] = []
    ys: List[float] = []
    hits = 0
    hit_n = 0
    priors: List[float] = []
    posts: List[float] = []
    for r in ledger.get("rows") or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("code") or "").strip()
        if shadow_codes and code not in shadow_codes:
            continue
        yhat_n = _lio._to_float(r.get("yhat_nowcast"))
        if yhat_n is None:
            continue
        oc = by_oc.get(code) or {}
        y_cc = _lio._to_float(oc.get("realized_h"))
        if y_cc is None:
            y_cc = _lio._to_float(oc.get("realized"))
        if y_cc is None:
            continue
        xs.append(float(yhat_n))
        ys.append(float(y_cc))
        hit = oc.get("sign_hit_nowcast")
        if hit is None:
            hit = _sign_hit(yhat_n, y_cc)
        if hit is not None:
            hit_n += 1
            if hit:
                hits += 1
        p0 = _lio._to_float(r.get("nowcast_x_prior"))
        if p0 is not None:
            priors.append(float(p0))
            posts.append(float(yhat_n))

    vs = (shadow.get("meta") or {}).get("vs_eod_book")
    nordhaus_meta = (shadow.get("meta") or {}).get("nordhaus_revision_slope")
    if not isinstance(vs, dict):
        vs = None
        try:
            from core.signal.cluster_live import load_nowcast_shadow_cluster_book

            live_sh = load_nowcast_shadow_cluster_book() or {}
            live_meta = (live_sh.get("meta") or {}) if isinstance(live_sh, dict) else {}
            vs = live_meta.get("vs_eod_book")
            if nordhaus_meta is None:
                nordhaus_meta = live_meta.get("nordhaus_revision_slope")
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            vs = None
    if not isinstance(vs, dict):
        vs = _nowcast_vs_eod_from_ledger(ledger)

    nordhaus = nordhaus_meta
    if nordhaus is None and len(priors) >= 3:
        try:
            from core.signal.nowcast_kf import nordhaus_revision_slope

            nordhaus = nordhaus_revision_slope(priors, posts)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            nordhaus = None

    ic = _spearman_ic(xs, ys)
    return {
        "success": True,
        "as_of": d,
        "horizon_days": h,
        "nowcast_ic_spearman": ic,
        "nowcast_n": len(xs),
        "nowcast_sign_hit_rate": (
            round(hits / hit_n, 4) if hit_n else None
        ),
        "nowcast_sign_hit_n": hit_n,
        "nordhaus_revision_slope": nordhaus,
        "shadow_membership": {
            "exists": not bool(shadow.get("empty")),
            "n_rows": len(shadow.get("rows") or []),
            "path": shadow.get("path"),
            "vs_eod_book": vs,
            "nordhaus_revision_slope": nordhaus_meta,
        },
        "_lio.ledger_path": ledger.get("path"),
        "_lio.outcomes_path": outcomes.get("path"),
        "y_spec_nowcast": "close[T]/close[T-1]−1（与 ŷ_trade / 涨跌同一口径）",
        "note": (
            "N3：IC/命中对 ŷ_nowcast↔涨跌（昨收口径）；Nordhaus 接近 0 才考虑升主排序。"
            "不替代 EOD / τ 复盘。对照分，不进决策。"
        ),
    }


def _blame_tag(
    *,
    sign_hit: Optional[bool],
    dominant: Optional[str],
    factor_ic: Optional[float],
    no_direction: bool,
    has_terms: bool,
    has_realized: bool,
) -> str:
    if no_direction:
        return "no_direction"
    if not has_realized:
        return "data_thin"
    if sign_hit is True:
        return "hit"
    if sign_hit is not False:
        return "data_thin"
    if not has_terms or not dominant:
        return "data_thin"
    if factor_ic is not None and factor_ic < 0:
        return "factor_fade"
    if factor_ic is not None and factor_ic >= 0:
        return "idiosyncratic"
    return "model_tilt"


def _factor_cn(name: Optional[str]) -> str:
    key = str(name or "").strip()
    if not key:
        return ""
    try:
        from core.signal.factor_registry import factor_label

        return factor_label(key) or key
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        return key


def _day_factor_ic_proxy(
    rows: Sequence[dict],
    outcomes: Dict[str, dict],
    factor_key: str,
) -> Optional[float]:
    """用账本 terms 的 z 与 realized 做简易截面相关（样本少时仅作提示）。"""
    xs: List[float] = []
    ys: List[float] = []
    for r in rows:
        code = str(r.get("code") or "")
        oc = outcomes.get(code) or {}
        realized = _lio._to_float(oc.get("realized_h"))
        if realized is None:
            continue
        z = None
        for t in r.get("formula_terms_top") or []:
            if str(t.get("key")) == factor_key:
                z = _lio._to_float(t.get("z"))
                if z is None:
                    z = _lio._to_float(t.get("contrib"))
                break
        if z is None:
            continue
        xs.append(z)
        ys.append(realized)
    if len(xs) < 3:
        return None
    try:
        from core.backtest.pool_ic import _pearson

        return _pearson(xs, ys)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        return None


def _empty_review_payload(d: str, h: int) -> Dict[str, Any]:
    """空账本时的复盘占位返回。"""
    return {
        "success": True,
        "empty": True,
        "as_of": d,
        "horizon_days": h,
        "error": None,
        "note": "无该日账本。请先跑分组落书、生成日报，或点「冻结今日打分」。",
        "summary": {},
        "wrong_rows": [],
        "scored_rows": [],
        "factor_blame": [],
        "industry_blame": [],
        "cluster_blame": [],
        "tau_shadow": _tau_shadow_review_slim(d, horizon_days=h),
        "nowcast_shadow": _nowcast_shadow_review_slim(d, horizon_days=h),
    }


def _prepare_review_inputs(
    d: str,
    ledger: Dict[str, Any],
    h: int,
    autofill: bool,
) -> Dict[str, Any]:
    """补挂 yhat_tau + 载 outcomes + 必要时 fill_outcomes。"""
    # 旧冻结账本无 yhat_tau → 复盘列 ŷ_τ 全是 —；按日线 PIT 补挂
    hydrate_meta: Dict[str, Any] = {}
    try:
        hydrate_meta = hydrate_ledger_yhat_tau(d, persist=True) or {}
        if int(hydrate_meta.get("hydrated") or 0) > 0:
            ledger = _lio.load_ledger(d)
    except Exception as exc:
        logger.exception('unexpected error in build_score_review')
        hydrate_meta = {"success": False, "error": str(exc)}
    outcomes = _lio.load_outcomes(d)
    need_tau_refresh = False
    if autofill and outcomes.get("by_code"):
        by_tmp = outcomes.get("by_code") or {}
        for r0 in ledger.get("rows") or []:
            if not isinstance(r0, dict):
                continue
            yt0 = _lio._to_float(r0.get("yhat_tau"))
            if yt0 is None:
                continue
            oc0 = by_tmp.get(str(r0.get("code") or "")) or {}
            if _lio._to_float(oc0.get("yhat_tau")) is None:
                need_tau_refresh = True
                break
    need_fill = (
        autofill
        and (
            outcomes.get("empty")
            or int(outcomes.get("horizon_days") or 0) != h
            or not outcomes.get("by_code")
            or int(hydrate_meta.get("hydrated") or 0) > 0
            or need_tau_refresh
        )
    )
    if need_fill:
        fill_outcomes(d, horizon_days=h)
        outcomes = _lio.load_outcomes(d)
    return {"ledger": ledger, "outcomes": outcomes, "hydrate_meta": hydrate_meta}


def _accumulate_review_stats(
    rows: List[Dict[str, Any]],
    by_code: Dict[str, Any],
) -> Dict[str, Any]:
    """单票方向命中/错票/归因累积（含 Y(τ) 校验分桶）。"""
    n = 0
    hits = 0
    wrong_long = 0
    wrong_short = 0
    no_dir = 0
    thin = 0
    wrong_rows: List[Dict[str, Any]] = []
    scored_rows: List[Dict[str, Any]] = []
    tag_counts: Dict[str, int] = {}
    factor_wrong: Dict[str, int] = {}
    industry_wrong: Dict[str, int] = {}
    cluster_wrong: Dict[str, int] = {}
    factor_ic_cache: Dict[str, Optional[float]] = {}
    # Y(τ) 校验分桶：n / hits（对 EOD 方向命中）
    y_check_stats: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        code = str(r.get("code") or "")
        oc = by_code.get(code) or {}
        yhat = _lio._to_float(r.get("yhat"))
        realized = _lio._to_float(oc.get("realized_h"))
        hit = oc.get("sign_hit")
        if hit is None and yhat is not None and realized is not None:
            hit = _sign_hit(yhat, realized)
        no_direction = bool(oc.get("no_direction")) or (
            yhat is not None and abs(float(yhat)) < _lio._YHAT_EPS
        )
        dominant = oc.get("dominant_factor")
        if not dominant:
            terms = r.get("formula_terms_top") or []
            if terms:
                dominant = terms[0].get("key")
        fic = None
        if dominant:
            if dominant not in factor_ic_cache:
                factor_ic_cache[dominant] = _day_factor_ic_proxy(rows, by_code, str(dominant))
            fic = factor_ic_cache.get(str(dominant))
        tag = _blame_tag(
            sign_hit=hit if isinstance(hit, bool) else None,
            dominant=str(dominant) if dominant else None,
            factor_ic=fic,
            no_direction=no_direction,
            has_terms=bool(r.get("formula_terms_top")),
            has_realized=realized is not None,
        )
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        # 复盘侧装配 / 回放 y_check（旧账本无字段时用 eod/tau 现场算）
        y_check_row = str(r.get("y_check") or "").strip() or None
        yhat_tau_preview = _lio._to_float(r.get("yhat_tau"))
        eod_rem_preview = _lio._to_float(r.get("yhat_eod_rem"))
        if eod_rem_preview is None:
            eod_rem_preview = _lio._to_float(r.get("yhat_eod"))
        if eod_rem_preview is None:
            eod_rem_preview = yhat
        if not y_check_row:
            try:
                from core.signal.y_state import resolve_eod_check

                y_check_row = resolve_eod_check(
                    eod_rem=eod_rem_preview,
                    y_tau=yhat_tau_preview,
                    head="blend" if (eod_rem_preview is not None and yhat_tau_preview is not None) else (
                        "single_eod" if yhat_tau_preview is None else "single_tau"
                    ),
                    disagree=(
                        abs(float(eod_rem_preview) - float(yhat_tau_preview))
                        if eod_rem_preview is not None and yhat_tau_preview is not None
                        else None
                    ),
                    sigma=None,
                    window="intraday",
                )
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
                y_check_row = None
        y_disagree_row = _lio._to_float(r.get("y_disagree"))
        if y_disagree_row is None and eod_rem_preview is not None and yhat_tau_preview is not None:
            try:
                y_disagree_row = round(
                    abs(float(eod_rem_preview) - float(yhat_tau_preview)), 6
                )
            except (TypeError, ValueError):
                y_disagree_row = None
        if no_direction:
            no_dir += 1
            continue
        if realized is None:
            thin += 1
            continue
        n += 1
        if y_check_row:
            bucket = y_check_stats.setdefault(
                y_check_row, {"n": 0, "hits": 0, "wrong": 0}
            )
            bucket["n"] += 1
            if hit is True:
                bucket["hits"] += 1
            elif hit is False:
                bucket["wrong"] += 1
        yhat_tau_row = yhat_tau_preview
        if yhat_tau_row is None:
            yhat_tau_row = _lio._to_float(oc.get("yhat_tau"))
        hit_tau = oc.get("sign_hit_tau")
        scored_rows.append(
            {
                "code": code,
                "name": r.get("name"),
                "yhat": yhat,
                "yhat_tau": yhat_tau_row,
                "yhat_eod_rem": eod_rem_preview,
                "y_check": y_check_row,
                "y_disagree": y_disagree_row,
                "eod_trust": _lio._to_float(r.get("eod_trust")),
                "realized_h": realized,
                "realized_tau": _lio._to_float(oc.get("realized_tau")),
                "hit": hit if isinstance(hit, bool) else None,
                "hit_tau": hit_tau if isinstance(hit_tau, bool) else None,
                "abs_err": oc.get("abs_err"),
                "abs_err_tau": oc.get("abs_err_tau"),
                "sector": r.get("sector"),
                "cluster_label": r.get("cluster_label"),
                "dominant_factor": dominant,
                "dominant_factor_label": _factor_cn(dominant) if dominant else None,
                "tag": tag,
            }
        )
        if hit is True:
            hits += 1
            continue
        if hit is False:
            if float(yhat or 0) > 0:
                wrong_long += 1
            else:
                wrong_short += 1
            if dominant:
                factor_wrong[str(dominant)] = factor_wrong.get(str(dominant), 0) + 1
            sec = str(r.get("sector") or "").strip() or "其他"
            industry_wrong[sec] = industry_wrong.get(sec, 0) + 1
            clab = str(r.get("cluster_label") or "").strip() or "—"
            cluster_wrong[clab] = cluster_wrong.get(clab, 0) + 1
            wrong_rows.append(
                {
                    "code": code,
                    "name": r.get("name"),
                    "yhat": yhat,
                    "realized_h": realized,
                    "abs_err": oc.get("abs_err"),
                    "cluster_label": r.get("cluster_label"),
                    "sector": r.get("sector") or sec,
                    "dominant_factor": dominant,
                    "dominant_factor_label": _factor_cn(dominant) if dominant else None,
                    "factor_ic_day": round(fic, 4) if fic is not None else None,
                    "tag": tag,
                    "formula_terms_top": (r.get("formula_terms_top") or [])[:3],
                }
            )

    return {
        "n": n,
        "hits": hits,
        "wrong_long": wrong_long,
        "wrong_short": wrong_short,
        "no_dir": no_dir,
        "thin": thin,
        "wrong_rows": wrong_rows,
        "scored_rows": scored_rows,
        "tag_counts": tag_counts,
        "factor_wrong": factor_wrong,
        "industry_wrong": industry_wrong,
        "cluster_wrong": cluster_wrong,
        "factor_ic_cache": factor_ic_cache,
        "y_check_stats": y_check_stats,
    }


def _build_by_y_check(y_check_stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Y(τ) 校验分桶汇总。"""
    by_y_check: List[Dict[str, Any]] = []
    for ck, st in sorted(
        y_check_stats.items(),
        key=lambda kv: -int(kv[1].get("n") or 0),
    ):
        nn = int(st.get("n") or 0)
        hh = int(st.get("hits") or 0)
        by_y_check.append(
            {
                "check": ck,
                "n": nn,
                "hits": hh,
                "wrong": int(st.get("wrong") or 0),
                "hit_rate": round(hh / nn, 4) if nn else None,
            }
        )
    return by_y_check


def _build_blame_lists(
    stats: Dict[str, Any],
    rows: List[Dict[str, Any]],
    by_code: Dict[str, Any],
) -> Dict[str, Any]:
    """因子 / 行业 / 集群 归因 Top 榜单。"""
    factor_wrong: Dict[str, int] = stats["factor_wrong"]
    industry_wrong: Dict[str, int] = stats["industry_wrong"]
    cluster_wrong: Dict[str, int] = stats["cluster_wrong"]
    factor_ic_cache: Dict[str, Optional[float]] = stats["factor_ic_cache"]
    factor_blame = []
    for fac, cnt in sorted(factor_wrong.items(), key=lambda kv: -kv[1])[:8]:
        fic = factor_ic_cache.get(fac)
        if fic is None:
            fic = _day_factor_ic_proxy(rows, by_code, fac)
        factor_blame.append(
            {
                "factor": fac,
                "factor_label": _factor_cn(fac),
                "wrong_count": cnt,
                "factor_ic_day": round(fic, 4) if fic is not None else None,
            }
        )
    industry_blame = [
        {"sector": sec, "wrong_count": cnt}
        for sec, cnt in sorted(industry_wrong.items(), key=lambda kv: -kv[1])[:8]
    ]
    cluster_blame = [
        {"cluster_label": lab, "wrong_count": cnt}
        for lab, cnt in sorted(cluster_wrong.items(), key=lambda kv: -kv[1])[:8]
    ]
    return {
        "factor_blame": factor_blame,
        "industry_blame": industry_blame,
        "cluster_blame": cluster_blame,
    }


def _build_blame_line(
    n: int,
    hits: int,
    hit_rate: Optional[float],
    thin: int,
    len_rows: int,
    factor_blame: List[Dict[str, Any]],
    industry_blame: List[Dict[str, Any]],
    d: str,
    h: int,
) -> Dict[str, Any]:
    """一行归因文案 + 薄样本对账日。"""
    blame_line = "样本不足"
    need_bar = None
    if n:
        if hit_rate is not None and hit_rate >= 0.55:
            blame_line = f"方向命中 {hit_rate:.0%}（{hits}/{n}）"
        elif factor_blame:
            top = factor_blame[0]
            ic = top.get("factor_ic_day")
            ic_s = f"，当日因子相关≈{ic}" if ic is not None else ""
            fac_cn = top.get("factor_label") or _factor_cn(top.get("factor"))
            blame_line = (
                f"方向命中 {hit_rate:.0%}（{hits}/{n}）；"
                f"错票常挂在 {fac_cn}（{top['wrong_count']} 次）{ic_s}"
            )
            if industry_blame:
                blame_line += f"；行业 {industry_blame[0]['sector']}×{industry_blame[0]['wrong_count']}"
        else:
            blame_line = f"方向命中 {hit_rate:.0%}（{hits}/{n}）"
    elif thin > 0:
        try:
            from core.market.calendar import next_trading_day

            need_bar = next_trading_day(d, n=h)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            need_bar = None
        if need_bar:
            blame_line = (
                f"薄样本 {thin}/{len_rows}：需要对账日 {need_bar} 的收盘价"
                f"（as_of+{h}）。盘中日线通常未入库，收盘后再回填。"
            )
        else:
            blame_line = (
                f"薄样本 {thin}/{len_rows}：本地日线未覆盖 as_of+{h}，尚无实现收益"
            )
    return {"blame_line": blame_line, "need_bar": need_bar}


def _maybe_horizon_fallback(
    d: str,
    h: int,
    autofill: bool,
    thin: int,
) -> Optional[Dict[str, Any]]:
    """h>1 全部薄样本时自动降到 h=1，避免 UI 只显示「命中 —」。"""
    fallback = build_score_review(d, horizon_days=1, autofill=autofill)
    fb_n = int((fallback.get("summary") or {}).get("n_scored") or 0)
    if fallback.get("success") and not fallback.get("empty") and fb_n > 0:
        fallback["horizon_fallback_from"] = h
        fallback["note"] = (
            f"h={h} 实现收益未齐（日线未覆盖 as_of+{h}，薄样本 {thin}）。"
            f"已自动改用 h=1。"
            + (" " + str(fallback.get("note") or "")).rstrip()
        )
        return fallback
    return None


def _assemble_review_payload(
    d: str,
    h: int,
    ledger: Dict[str, Any],
    outcomes: Dict[str, Any],
    rows: List[Dict[str, Any]],
    rows_universe: List[Dict[str, Any]],
    stats: Dict[str, Any],
    by_y_check: List[Dict[str, Any]],
    factor_blame: List[Dict[str, Any]],
    industry_blame: List[Dict[str, Any]],
    cluster_blame: List[Dict[str, Any]],
    blame_line: str,
    need_bar: Optional[str],
    hydrate_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """装配方向复盘报告最终 payload。"""
    n = stats["n"]
    hits = stats["hits"]
    thin = stats["thin"]
    no_dir = stats["no_dir"]
    wrong_long = stats["wrong_long"]
    wrong_short = stats["wrong_short"]
    wrong_rows = stats["wrong_rows"]
    scored_rows = stats["scored_rows"]
    tag_counts = stats["tag_counts"]
    hit_rate = stats["hit_rate"]
    return {
        "success": True,
        "empty": False,
        "as_of": d,
        "horizon_days": h,
        "_lio.ledger_path": ledger.get("path"),
        "_lio.outcomes_path": outcomes.get("path"),
        "n_ledger": len(rows),
        "n_ledger_universe": len(rows_universe),
        "summary": {
            "n_scored": n,
            "n_book": len(rows),
            "n_universe": len(rows_universe),
            "hit_rate": hit_rate,
            "hits": hits,
            "wrong": len(wrong_rows),
            "wrong_long": wrong_long,
            "wrong_short": wrong_short,
            "no_direction": no_dir,
            "data_thin": thin,
            "tag_counts": tag_counts,
            "blame_line": blame_line,
            "need_bar_date": need_bar if (n == 0 and thin > 0) else None,
            "by_y_check": by_y_check,
        },
        "wrong_rows": wrong_rows[:80],
        "scored_rows": scored_rows[:200],
        "factor_blame": factor_blame,
        "industry_blame": industry_blame,
        "cluster_blame": cluster_blame,
        "suggested_horizon": 1 if (n == 0 and thin > 0 and h > 1) else None,
        "note": (
            (
                f"h={h} 尚无实现收益（薄样本 {thin}/{len(rows)}）。"
                + (
                    f"需要对账日 {need_bar} 收盘价；盘中通常未入库。"
                    if need_bar
                    else "请刷新日线、改小 Horizon，或选更早决策日后再「回填收益」。"
                )
                if (n == 0 and thin > 0)
                else ""
            )
            + "方向复盘：sign(ŷ) vs sign(r_h)；|ŷ|<0.05% 视为无方向。"
            "标签：factor_fade=主导因子当日截面相关为负；"
            "idiosyncratic=因子未坏但个股反；model_tilt=分解不足时的模型偏置兜底。"
        ).strip(),
        "refit_hint": (
            "错票偏多时建议到上方「跑分组」重估组 β（不自动改权）。"
            if (hit_rate is not None and hit_rate < 0.5 and n >= 5)
            else None
        ),
        "tau_shadow": _tau_shadow_review_slim(d, horizon_days=h),
        "nowcast_shadow": _nowcast_shadow_review_slim(d, horizon_days=h),
        "yhat_tau_hydrate": {
            "hydrated": int(hydrate_meta.get("hydrated") or 0),
            "note": hydrate_meta.get("note"),
        }
        if hydrate_meta
        else None,
    }


def build_score_review(
    as_of: Optional[str] = None,
    *,
    horizon_days: int = 3,
    autofill: bool = True,
) -> Dict[str, Any]:
    """方向复盘报告：命中率 + 错票 + 简易归因标签。"""
    d = date_key(as_of) or default_as_of()
    h = max(1, min(int(horizon_days or 3), 10))
    ledger = _lio.load_ledger(d)
    if ledger.get("empty"):
        return _empty_review_payload(d, h)

    inputs = _prepare_review_inputs(d, ledger, h, autofill)
    ledger = inputs["ledger"]
    outcomes = inputs["outcomes"]
    hydrate_meta = inputs["hydrate_meta"]

    by_code = outcomes.get("by_code") or {}
    rows_universe = [r for r in (ledger.get("rows") or []) if isinstance(r, dict)]
    rows = _lio.rows_for_book_review(rows_universe)

    stats = _accumulate_review_stats(rows, by_code)
    stats["wrong_rows"].sort(key=lambda x: -float(x.get("abs_err") or 0))
    hit_rate = round(stats["hits"] / stats["n"], 4) if stats["n"] else None
    stats["hit_rate"] = hit_rate

    by_y_check = _build_by_y_check(stats["y_check_stats"])
    blame_lists = _build_blame_lists(stats, rows, by_code)
    factor_blame = blame_lists["factor_blame"]
    industry_blame = blame_lists["industry_blame"]
    cluster_blame = blame_lists["cluster_blame"]

    blame = _build_blame_line(
        stats["n"], stats["hits"], hit_rate, stats["thin"],
        len(rows), factor_blame, industry_blame, d, h,
    )
    blame_line = blame["blame_line"]
    need_bar = blame["need_bar"]

    # h>1 全部薄样本时自动降到 h=1，避免 UI 只显示「命中 —」
    if stats["n"] == 0 and stats["thin"] > 0 and h > 1:
        fallback = _maybe_horizon_fallback(d, h, autofill, stats["thin"])
        if fallback is not None:
            return fallback

    return _assemble_review_payload(
        d, h, ledger, outcomes, rows, rows_universe, stats,
        by_y_check, factor_blame, industry_blame, cluster_blame,
        blame_line, need_bar, hydrate_meta,
    )


def _tau_shadow_review_slim(as_of: str, *, horizon_days: int = 1) -> Dict[str, Any]:
    """复盘附带 A2 τ 摘要（不二次 autofill）。"""
    try:
        pack = build_tau_shadow_review(
            as_of, horizon_days=max(1, int(horizon_days or 1)), autofill=False
        )
    except Exception as exc:
        logger.exception('unexpected error in _tau_shadow_review_slim')
        return {"success": False, "error": str(exc)}
    if not isinstance(pack, dict):
        return {"success": False}
    sh = pack.get("shadow_membership") or {}
    return {
        "success": bool(pack.get("success")),
        "as_of": pack.get("as_of"),
        "tau_ic_spearman": pack.get("tau_ic_spearman"),
        "tau_n": pack.get("tau_n"),
        "tau_sign_hit_rate": pack.get("tau_sign_hit_rate"),
        "tau_sign_hit_n": pack.get("tau_sign_hit_n"),
        "shadow_exists": bool(sh.get("exists")),
        "shadow_n": sh.get("n_rows"),
        "vs_eod": sh.get("vs_eod_book"),
        "y_spec_tau": pack.get("y_spec_tau"),
        "note": pack.get("note"),
    }


def _nowcast_shadow_review_slim(as_of: str, *, horizon_days: int = 1) -> Dict[str, Any]:
    """复盘附带 N3 nowcast 摘要（不二次 autofill）。"""
    try:
        pack = build_nowcast_shadow_review(
            as_of, horizon_days=max(1, int(horizon_days or 1)), autofill=False
        )
    except Exception as exc:
        logger.exception('unexpected error in _nowcast_shadow_review_slim')
        return {"success": False, "error": str(exc)}
    if not isinstance(pack, dict):
        return {"success": False}
    sh = pack.get("shadow_membership") or {}
    return {
        "success": bool(pack.get("success")),
        "as_of": pack.get("as_of"),
        "nowcast_ic_spearman": pack.get("nowcast_ic_spearman"),
        "nowcast_n": pack.get("nowcast_n"),
        "nowcast_sign_hit_rate": pack.get("nowcast_sign_hit_rate"),
        "nowcast_sign_hit_n": pack.get("nowcast_sign_hit_n"),
        "nordhaus_revision_slope": pack.get("nordhaus_revision_slope"),
        "shadow_exists": bool(sh.get("exists")),
        "shadow_n": sh.get("n_rows"),
        "vs_eod": sh.get("vs_eod_book"),
        "y_spec_nowcast": pack.get("y_spec_nowcast"),
        "note": pack.get("note"),
    }

