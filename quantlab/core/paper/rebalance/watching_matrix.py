"""观察池实时算分 + rank_lots 预演/落账。

手动路径：dry_run 预演 → 确认后同算法落账（改 paper 持仓/现金）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Collection, Dict, List, Optional, Sequence, Tuple

from core.paper.rebalance.rank_lots import AUX_YHAT_KEYS, tip_explain_fields

logger = logging.getLogger(__name__)


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


def _ymd(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().replace("Z", "")
    d = s[:10]
    if len(d) == 10 and d[4] == "-" and d[7] == "-":
        return d
    return None


def _open_date_of(
    holding: Optional[dict],
    fallback: Optional[str] = None,
) -> Optional[str]:
    """持仓最早开日（lots.bought_date 优先，避免 ts=墙钟把开日写成今天）。"""
    dates: List[str] = []
    if isinstance(holding, dict):
        try:
            from core.paper.tplus1 import ensure_lots

            ensure_lots(holding)
        except Exception:  # noqa: BLE001
            logger.debug("ensure_lots failed in _open_date_of", exc_info=True)
        lots = holding.get("lots") if isinstance(holding.get("lots"), list) else []
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            d = _ymd(lot.get("bought_date")) or _ymd(lot.get("bought_at"))
            if d:
                dates.append(d)
        if not dates:
            d = _ymd(holding.get("bought_date")) or _ymd(holding.get("bought_at"))
            if d:
                dates.append(d)
    if dates:
        return min(dates)
    return _ymd(fallback)


def _watching_codes(*, include_held: Sequence[str] = ()) -> Tuple[List[str], Dict[str, Any]]:
    meta: Dict[str, Any] = {"source": "watching", "n_watch": 0, "n_held_extra": 0}
    name_by_code: Dict[str, str] = {}
    try:
        from core.watching.store import read_watching, refresh_watchlist, watchlist_names_for

        uni = read_watching()
        codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        if not codes:
            refreshed = refresh_watchlist(uni)
            codes = [str(c).strip() for c in (refreshed.get("watchlist") or []) if str(c).strip()]
            uni = refreshed
        meta["n_watch"] = len(codes)
        meta["watching_name"] = uni.get("name")
        try:
            names = watchlist_names_for(uni)
            for c, n in zip(codes, names):
                nn = str(n or "").strip()
                if c and nn and nn != c:
                    name_by_code[c] = nn
        except Exception:  # noqa: BLE001
            logger.debug("watchlist names map skipped", exc_info=True)
    except FileNotFoundError:
        return [], {"error": "watching.json 不存在", "source": "watching"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("read watching failed: %s", exc, exc_info=True)
        return [], {"error": str(exc), "source": "watching"}

    seen = set(codes)
    extra = 0
    for raw in include_held or []:
        c = str(raw or "").strip()
        if c and c not in seen:
            codes.append(c)
            seen.add(c)
            extra += 1
    meta["n_held_extra"] = extra
    meta["n_total"] = len(codes)
    meta["name_by_code"] = name_by_code
    return codes, meta


def _resolve_rank_day_cube(
    items: Sequence[dict],
    *,
    day_cube: Any = None,
    clock: str = "open",
) -> Any:
    """复用已建 Cube；否则按行上湖日尝试 ensure（与主头同全池键）。"""
    if day_cube is not None:
        return day_cube
    rows = [it for it in (items or []) if isinstance(it, dict)]
    if len(rows) < 2:
        return None
    try:
        from core.research.batch_score import ensure_day_cube_for_items
        from core.research.materialize.coverage import read_cube_codes

        day = _live_rank_asof(rows)
        uh = None
        try:
            uh = read_cube_codes() or None
        except Exception:  # noqa: BLE001
            uh = None
        return ensure_day_cube_for_items(
            rows,
            date=day,
            clock=str(clock or "open"),
            persist=False,
            universe_codes=uh,
        )
    except Exception:  # noqa: BLE001
        logger.debug("resolve rank day_cube failed", exc_info=True)
        return None


def _attach_oo_rank_scores(
    items: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
    clock: str = "open",
) -> None:
    """ŷ_oo_rank：有已启用模型则写入当日截面 1..n 名次；缺则跳过。不改 y_oo_fuse；过入场后作买序。

    截面 z 只用全池 L2；``score_codes`` 只限制写名次。
    模型 ``train_universe=tier_a`` 不影响打分 μ/σ。
    """
    try:
        from core.research.oo_rank_lambdarank import apply_oo_rank_scores, load_oo_rank_model

        # live 调仓只用执行套；缺则跳过，不静默换研究套。
        doc = load_oo_rank_model(prefer_research=False)
        if doc:
            cube = _resolve_rank_day_cube(
                items, day_cube=day_cube, clock=clock
            )
            apply_oo_rank_scores(
                list(items or []),
                model_doc=doc,
                score_codes=score_codes,
                day_cube=cube,
                clock=str(clock or "open"),
            )
    except Exception:  # noqa: BLE001
        logger.debug("attach live y_oo_rank failed", exc_info=True)


def _attach_tc_rank_scores(
    items: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
    clock: str = "open",
) -> None:
    """ŷ_τc_rank：有执行套模型则写入当日截面名次；缺则跳过。宇宙语义同 oo_rank。"""
    try:
        from core.research.tc_rank import apply_tc_rank_scores, load_tc_rank_model

        doc = load_tc_rank_model(prefer_research=False)
        if doc:
            cube = _resolve_rank_day_cube(
                items, day_cube=day_cube, clock=clock
            )
            apply_tc_rank_scores(
                list(items or []),
                model_doc=doc,
                score_codes=score_codes,
                day_cube=cube,
                clock=str(clock or "open"),
            )
    except Exception:  # noqa: BLE001
        logger.debug("attach live y_tc_rank failed", exc_info=True)


# 持仓表 / 盯盘 / 交易记录展示用。不含公式拆解（拆解走 tip_explain）。
LIVE_RANK_KEYS = (
    "y_oo_rank",
    "y_oo_rank_hat",
    "y_oo_rank_n",
    "y_oo_rank_score",
    "predicted_score_oo_rank",
    "y_tc_rank",
    "y_tc_rank_hat",
    "y_tc_rank_n",
    "y_tc_rank_score",
    "predicted_score_tc_rank",
    "formula_terms_oo_rank",
    "score_formula_terms_oo_rank",
    "formula_terms_tc_rank",
    "score_formula_terms_tc_rank",
)


def _live_rank_snapshot_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "live_rank_snapshot.json")


def _rank_slice(item: dict) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in LIVE_RANK_KEYS:
        if item.get(key) is not None:
            out[key] = item.get(key)
    return out


def save_live_rank_snapshot(items: Sequence[dict], *, as_of: str) -> None:
    """把当日观察池名次落到 live 快照，供持仓表在未重算全池时读取。"""
    import json
    import os

    day = str(as_of or "")[:10]
    if len(day) < 10:
        return
    by_code: Dict[str, Any] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if not code:
            continue
        packed = _rank_slice(it)
        if packed:
            by_code[code] = packed
    if not by_code:
        return
    try:
        from core.signal.score_display import json_safe

        payload = json_safe({"as_of": day, "by_code": by_code})
    except Exception:  # noqa: BLE001
        payload = {"as_of": day, "by_code": by_code}
    path = _live_rank_snapshot_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)


def load_live_rank_snapshot(*, as_of: str) -> Dict[str, Dict[str, Any]]:
    import json

    day = str(as_of or "")[:10]
    if len(day) < 10:
        return {}
    path = _live_rank_snapshot_path()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or str(doc.get("as_of") or "")[:10] != day:
        return {}
    raw = doc.get("by_code")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for code, row in raw.items():
        c = str(code or "").strip()
        if c and isinstance(row, dict):
            out[c] = row
    return out


def _copy_rank_fields(dst: dict, src: dict, *, overwrite: bool) -> bool:
    wrote = False
    for key in LIVE_RANK_KEYS:
        val = src.get(key)
        if val is None:
            continue
        if not overwrite and dst.get(key) is not None:
            continue
        dst[key] = val
        wrote = True
    return wrote


def _lake_cover_ok(n_rows: int, n_codes: int) -> bool:
    if n_rows < 2 or n_codes < 2:
        return False
    if n_codes <= 12:
        return n_rows >= n_codes - 1
    return n_rows >= 8 and n_rows >= int(0.5 * n_codes)


def _feature_overlap_ok(sample: dict, names: Sequence[str]) -> bool:
    cols = [str(n) for n in names if str(n or "").strip()]
    if not cols or not isinstance(sample, dict):
        return False
    hit = sum(1 for name in cols if sample.get(name) is not None)
    return hit >= max(4, int(0.4 * len(cols)))


def _live_rank_asof(items: Optional[Sequence[dict]] = None) -> str:
    """Live 名次日：优先行上 ``lake_score_day`` / ``as_of``，否则 ŷ_oo 周期日。"""
    for it in items or []:
        if not isinstance(it, dict):
            continue
        day = str(it.get("lake_score_day") or it.get("as_of") or "")[:10]
        if len(day) >= 10:
            return day
    try:
        from core.signal.session_pit import oo_cycle_date

        day = str(oo_cycle_date() or "")[:10]
        if len(day) >= 10:
            return day
    except Exception:  # noqa: BLE001
        logger.debug("live rank asof oo_cycle_date failed", exc_info=True)
    from datetime import date

    return date.today().isoformat()


def _stamp_ranks_from_lake(focus: Sequence[dict]) -> None:
    """用 Feature Lake 的 oo_rank / tc_rank 行编观察池名次，写回 focus。

    日界与湖出分一致（``lake_score_day`` / 周期日候选）。只补 focus 上还没有的键。
    特征与模型对不上、或湖覆盖不够，则跳过该头。
    """
    from core.research.feature_lake import load_day_rows
    from core.research.lake_bridge import FAMILY_OO_RANK, FAMILY_TC_RANK

    held = [
        str(it.get("stock_code") or "").strip()
        for it in focus
        if isinstance(it, dict) and str(it.get("stock_code") or "").strip()
    ]
    if not held:
        return
    codes, _meta = _watching_codes(include_held=held)
    if len(codes) < 2:
        return
    cycle = _live_rank_asof(focus)
    oo_rows: Dict[str, Any] = {}
    tc_rows: Dict[str, Any] = {}
    day = ""
    for cand in _lake_score_day_candidates(cycle) or [cycle]:
        oo_try = load_day_rows(FAMILY_OO_RANK, codes, cand) or {}
        tc_try = load_day_rows(FAMILY_TC_RANK, codes, cand) or {}
        if _lake_cover_ok(len(oo_try), len(codes)) or _lake_cover_ok(
            len(tc_try), len(codes)
        ):
            oo_rows, tc_rows, day = oo_try, tc_try, cand
            break
    if not day:
        return
    oo_ok = _lake_cover_ok(len(oo_rows), len(codes))
    tc_ok = _lake_cover_ok(len(tc_rows), len(codes))
    if oo_ok:
        try:
            from core.research.oo_rank_lambdarank import (
                _oo_rank_feature_names,
                _resolve_oo_rank_fit,
                load_oo_rank_model,
            )

            doc = load_oo_rank_model(prefer_research=False)
            model = _resolve_oo_rank_fit(model_doc=doc)
            names = _oo_rank_feature_names(model) if isinstance(model, dict) else []
            sample = next(iter(oo_rows.values()), {})
            if not _feature_overlap_ok(sample, names):
                oo_ok = False
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank lake overlap check failed", exc_info=True)
            oo_ok = False
    if tc_ok:
        try:
            from core.research.oo_rank_lambdarank import _oo_rank_feature_names
            from core.research.tc_rank import _resolve_tc_rank_fit, load_tc_rank_model

            doc = load_tc_rank_model(prefer_research=False)
            model = _resolve_tc_rank_fit(doc)
            names = _oo_rank_feature_names(model) if isinstance(model, dict) else []
            sample = next(iter(tc_rows.values()), {})
            if not _feature_overlap_ok(sample, names):
                tc_ok = False
        except Exception:  # noqa: BLE001
            logger.debug("tc_rank lake overlap check failed", exc_info=True)
            tc_ok = False
    if not oo_ok and not tc_ok:
        return
    _, fit_meta = _apply_universe_fit_tier_filter(codes, keep=held)
    score_codes = fit_meta.get("tradable_codes")
    if not isinstance(score_codes, (list, tuple, set)):
        score_codes = None
    batch: List[dict] = []
    for code in codes:
        row: Dict[str, Any] = {"stock_code": code, "lake_score_day": day}
        if oo_ok and isinstance(oo_rows.get(code), dict):
            row["sub_scores"] = dict(oo_rows[code])
        if tc_ok and isinstance(tc_rows.get(code), dict):
            row["features_tau"] = dict(tc_rows[code])
        if row.get("sub_scores") or row.get("features_tau"):
            batch.append(row)
    if len(batch) < 2:
        return
    if oo_ok:
        _attach_oo_rank_scores(batch, score_codes=score_codes, clock="open")
    if tc_ok:
        _attach_tc_rank_scores(batch, score_codes=score_codes, clock="open")
    by_code = {
        str(it.get("stock_code") or "").strip(): it
        for it in batch
        if str(it.get("stock_code") or "").strip()
    }
    for it in focus:
        if not isinstance(it, dict):
            continue
        src = by_code.get(str(it.get("stock_code") or "").strip())
        if isinstance(src, dict):
            _copy_rank_fields(it, src, overwrite=False)


def stamp_live_ranks(focus: Sequence[dict]) -> None:
    """给已打分的持仓行补 oo_rank / τc_rank。

    先用同周期日调仓快照（与预演/落账同一截面）。缺的名字再试 Feature Lake。
    两侧都没有则留空，表上显示 —。
    """
    rows = [it for it in focus or [] if isinstance(it, dict)]
    if not rows:
        return
    asof = _live_rank_asof(rows)
    snap = load_live_rank_snapshot(as_of=asof)
    if not snap:
        for cand in _lake_score_day_candidates(asof):
            if cand == asof:
                continue
            snap = load_live_rank_snapshot(as_of=cand)
            if snap:
                break
    if snap:
        for it in rows:
            src = snap.get(str(it.get("stock_code") or "").strip())
            if isinstance(src, dict):
                _copy_rank_fields(it, src, overwrite=False)
    pending = [
        it
        for it in rows
        if it.get("y_oo_rank") is None or it.get("y_tc_rank") is None
    ]
    if not pending:
        return
    try:
        _stamp_ranks_from_lake(pending)
    except Exception:  # noqa: BLE001
        logger.debug("stamp live ranks from lake failed", exc_info=True)


def _apply_universe_fit_tier_filter(
    codes: Sequence[str],
    *,
    keep: Sequence[str] = (),
) -> Tuple[List[str], Dict[str, Any]]:
    """可预测性分档 live 闸的元数据。

    截面 z 用调用方传入的整段观察池（调用方勿用返回列表缩小打分池）。
    ``tradable_codes``：允许写 ŷ/名次并开仓；``force_exit_held``：掉档持仓硬清仓。
    无 active 时 ``tradable_codes`` 为 None（全员打分）。
    """
    from core.research.predictability_tiers import filter_codes_by_predictability_live

    return filter_codes_by_predictability_live(codes, keep=keep)


def _mark_predictability_tier_exits(
    item_by_code: Dict[str, dict],
    *,
    force_exit_held: Sequence[str],
    allowed_tiers: Sequence[str],
) -> None:
    """持仓掉出 live 允许档 → hard_reject，rank_lots 清仓。"""
    exits = [str(c).strip() for c in (force_exit_held or []) if str(c).strip()]
    if not exits:
        return
    allow = [
        str(t).strip().upper()
        for t in (allowed_tiers or [])
        if str(t or "").strip()
    ]
    label = "".join(allow) if allow else "A"
    why = f"可预测性非{label}档 清仓"
    for code in exits:
        item = item_by_code.get(code)
        if not isinstance(item, dict):
            item = {"stock_code": code}
            item_by_code[code] = item
        item["hard_reject"] = True
        item["reject_reason"] = why
        item["predictability_tier_exit"] = True


def _mark_predictability_tier_blocks(
    item_by_code: Dict[str, dict],
    *,
    tradable_codes: Any,
    allowed_tiers: Sequence[str],
) -> None:
    """不在允许档、且还没标清仓的名字：留在截面特征里（供 z），禁止新开。"""
    if not isinstance(tradable_codes, (list, tuple, set)):
        return
    allow = {str(c).strip() for c in tradable_codes if str(c).strip()}
    label = "".join(
        str(t).strip().upper()
        for t in (allowed_tiers or [])
        if str(t or "").strip()
    ) or "A"
    why = f"可预测性非{label}档 不开仓"
    for code, item in item_by_code.items():
        if not isinstance(item, dict) or code in allow or item.get("hard_reject"):
            continue
        item["hard_reject"] = True
        item["reject_reason"] = why
        item["predictability_tier_block"] = True


def _resolve_report_name(
    code: str,
    *,
    item: Optional[dict] = None,
    paper: Optional[dict] = None,
    name_by_code: Optional[Dict[str, str]] = None,
) -> str:
    """报告行股票名：signal → 持仓 → 观察池 → a_code_name。"""
    c = str(code or "").strip()
    fallback = ""
    if isinstance(item, dict):
        fallback = str(item.get("stock_name") or "").strip()
    if paper and isinstance(paper.get("holdings"), list):
        for h in paper["holdings"]:
            if not isinstance(h, dict):
                continue
            if str(h.get("stock_code") or "").strip() != c:
                continue
            hn = str(h.get("stock_name") or "").strip()
            if hn and hn != c:
                return hn
            break
    try:
        from core.t0.intraday import resolve_stock_name

        return resolve_stock_name(
            c, fallback=fallback, name_by_code=name_by_code or {}
        ) or fallback or c
    except Exception:  # noqa: BLE001
        logger.debug("resolve_stock_name failed for %s", c, exc_info=True)
        if fallback and fallback != c:
            return fallback
        mapped = (name_by_code or {}).get(c) or ""
        return mapped or fallback or c


def live_rebalance_wants_minute_tau(clock: Optional[str]) -> bool:
    """启动时间晚于 09:30 才用当天 5m 重算 ŷ_τc。

    ≤09:30 与历史回测成交钟 09:30 同口径：开盘 Z，不拉分钟线。
    """
    head = str(clock or "09:30").replace("：", ":").strip()[:5]
    return bool(head) and head > "09:30"


def score_session_from_lake(
    codes: Sequence[str],
    *,
    paper: Optional[dict] = None,
) -> Tuple[List[dict], List[dict]]:
    """观察池并上 ``codes``，读 ŷ_oo 周期日 L1 与同钟 L2（缺覆盖可回退上一交易日）。

    缺覆盖抛 ``MaterializeRequired``。
    """
    from datetime import datetime

    held = [str(c).strip() for c in codes if str(c or "").strip()]
    uni, _meta = _watching_codes(include_held=held)
    pool = uni or held
    clock = ""
    try:
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        clock = str((get_rank_lot_cfg(paper or {}) or {}).get("live_fill_clock") or "")
    except Exception:  # noqa: BLE001
        logger.debug("live fill clock unavailable", exc_info=True)
        clock = ""
    if len(clock) < 4:
        clock = datetime.now().strftime("%H:%M")
    return _score_pool_from_lake(
        pool,
        use_minute_tau=live_rebalance_wants_minute_tau(clock),
        tau_clock=clock,
    )


def _consumer_score_clock(use_minute_tau: Optional[bool], tau_clock: Optional[str]) -> str:
    """消费路径的 L2 钟：开盘为 ``open``，更晚的成交钟对齐物化网格（``HHMM``）。"""
    if not use_minute_tau:
        return "open"
    from core.research.lake_bridge import normalize_tau_clock

    clock = normalize_tau_clock(str(tau_clock or "open"))
    return "open" if clock == "open" else clock


def _lake_score_day_candidates(cycle_day: str) -> List[str]:
    """出分日候选：周期日 → 完整日线 as-of → 上一交易日（去重保序）。"""
    out: List[str] = []
    seen = set()

    def _add(raw: Any) -> None:
        day = str(raw or "")[:10]
        if len(day) < 10 or day in seen:
            return
        seen.add(day)
        out.append(day)

    _add(cycle_day)
    try:
        from core.market.calendar import expected_latest_daily_bar_date

        _add(expected_latest_daily_bar_date())
    except Exception:  # noqa: BLE001
        logger.debug("lake score candidates: expected_latest failed", exc_info=True)
    try:
        from core.market.calendar import prev_trading_day

        if cycle_day:
            _add(prev_trading_day(cycle_day))
    except Exception:  # noqa: BLE001
        logger.debug("lake score candidates: prev_trading_day failed", exc_info=True)
    return out


def _report_l2_gap_only(report: Dict[str, Any]) -> bool:
    """L1 族已齐，缺口只在截面立方（观察池变或未落盘）。"""
    if not isinstance(report, dict) or report.get("ok"):
        return False
    problems = report.get("problems")
    if not isinstance(problems, list) or not problems:
        return False
    for problem in problems:
        if not isinstance(problem, dict):
            return False
        if problem.get("family") or str(problem.get("layer") or "") == "l1":
            return False
        if str(problem.get("layer") or "") != "l2" and not problem.get("clock"):
            return False
    return True


def _resolve_lake_score_day(
    codes: Sequence[str],
    *,
    families: Sequence[str],
    clock: str,
    cube_codes: Optional[Sequence[str]] = None,
) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """选有 L1/L2 覆盖的出分日；全缺则返回最后一次覆盖报告。

    Returns:
        (day, coverage_report, fallback_from_cycle_or_None)
    """
    from core.research.materialize.coverage import assess_coverage
    from core.signal.session_pit import oo_cycle_date

    cycle_day = str(oo_cycle_date() or "")[:10]
    candidates = _lake_score_day_candidates(cycle_day)
    cube_arg = list(cube_codes) if cube_codes is not None and len(cube_codes) >= 2 else None
    last_report: Dict[str, Any] = {
        "ok": False,
        "error_code": "materialize_required",
        "error": "无 ŷ_oo 周期日" if not cycle_day else "请先日线物化 L1/L2",
        "hint": "日线物化",
    }
    for day in candidates:
        try:
            report = assess_coverage(
                codes,
                [day],
                families=list(families),
                clocks=[clock],
                cube_codes=cube_arg,
            )
        except Exception as exc:  # noqa: BLE001 — 覆盖检查异常当缺覆盖，试下一候选
            logger.warning("assess_coverage failed day=%s: %s", day, exc, exc_info=True)
            last_report = {
                "ok": False,
                "error_code": "materialize_required",
                "error": f"覆盖检查失败（{day}）：{exc}",
                "hint": "日线物化",
            }
            continue
        last_report = report if isinstance(report, dict) else last_report
        l2_gap = _report_l2_gap_only(report)
        if report.get("ok") or l2_gap:
            fallback_from = cycle_day if day != cycle_day else None
            if l2_gap:
                report = dict(report)
                report["l2_gap_only"] = True
                logger.info(
                    "lake score day %s L1 ok, cube gap (pool μ/σ)",
                    day,
                )
            elif fallback_from:
                logger.info(
                    "lake score day fallback cycle=%s -> %s (open L1/L2 未齐)",
                    cycle_day,
                    day,
                )
            return day, report, fallback_from
    if isinstance(last_report, dict):
        last_report = dict(last_report)
        last_report.setdefault("tried_days", list(candidates))
        last_report.setdefault("cycle_day", cycle_day or None)
    return cycle_day or "", last_report, None


def _cube_family_feats(feats: Dict[str, float]) -> Tuple[dict, dict, dict]:
    """立方列按模型前缀拆回 oo / τ / co。裸名不灌进去，避免别的族盖住 canon。"""
    oo: Dict[str, float] = {}
    tau: Dict[str, float] = {}
    co: Dict[str, float] = {}
    for key, val in feats.items():
        if key.startswith("z_oo_") or key.startswith("raw_aoo_"):
            oo[key] = val
        if key.startswith("z_co_") or key.startswith("raw_aco_"):
            co[key] = val
        if key.startswith(("raw_atc_", "z_τc_", "z_tc_", "z_τh_")):
            tau[key] = val
    # ŷ_co 的活跃列主要是 raw_aoo_*，和 ŷ_oo 共用同一批立方值
    if oo:
        for key, val in oo.items():
            if key.startswith("raw_aoo_"):
                co[key] = val
    return oo, tau, co


def _items_from_day_cube(cube: Any, day: str) -> List[dict]:
    """用当日立方的 ``X_raw`` 组装出分行，不再按票打开 L1 全历史。"""
    import numpy as np

    X = np.asarray(getattr(cube, "X_raw", None))
    names = [str(n) for n in (getattr(cube, "names", None) or [])]
    codes = list(getattr(cube, "codes", None) or [])
    if X.ndim != 2 or not names or not codes:
        return []
    n = min(len(codes), int(X.shape[0]))
    p = min(len(names), int(X.shape[1]))
    items: List[dict] = []
    for i in range(n):
        code = str(codes[i] or "").strip()
        if not code:
            continue
        row = X[i, :p]
        finite = np.isfinite(row)
        if not bool(finite.any()):
            continue
        feats = {names[j]: float(row[j]) for j in range(p) if bool(finite[j])}
        oo, tau, co = _cube_family_feats(feats)
        if not oo and not tau and not co:
            continue
        item: Dict[str, Any] = {
            "stock_code": code,
            "as_of": day,
            "date": day,
            "trade_date": day,
            "success": True,
            "_score_source": "day_cube",
            "lake_score_day": day,
        }
        if oo:
            item["sub_scores"] = oo
        if tau:
            item["features_tau"] = tau
        if co:
            item["features_co"] = co
            item["features_on"] = dict(co)
        gap = feats.get("gap_pct")
        if gap is None:
            gap = tau.get("gap_pct")
        if gap is not None:
            item["gap_pct"] = gap
        items.append(item)
    return items


def _cube_z_matrix(cube: Any):
    """当日 raw → 截面 z。缺测、μ 无效或 σ 过小记 0（预测里等于填当天均值）。"""
    import numpy as np

    X = np.asarray(getattr(cube, "X_raw", None), dtype=np.float64)
    mu = np.asarray(getattr(cube, "z_mean", None), dtype=np.float64)
    sd = np.asarray(getattr(cube, "z_std", None), dtype=np.float64)
    if X.ndim != 2 or mu.shape[0] != X.shape[1] or sd.shape[0] != X.shape[1]:
        return None
    valid = np.isfinite(X) & np.isfinite(mu) & np.isfinite(sd) & (sd >= 1e-12)
    Z = np.zeros(X.shape, dtype=np.float64)
    np.subtract(X, mu, out=Z, where=valid)
    np.divide(Z, sd, out=Z, where=valid)
    return Z


def _dot_head(
    Z: Any,
    names: Sequence[str],
    coefficients: Dict[str, Any],
    intercept: float,
    active: Sequence[str],
):
    """ŷ = intercept + β·z。缺列当 z=0。"""
    import numpy as np

    index = {str(n): i for i, n in enumerate(names)}
    cols: List[int] = []
    betas: List[float] = []
    for name in active:
        j = index.get(str(name))
        if j is None:
            continue
        try:
            beta = float(coefficients.get(name))
        except (TypeError, ValueError):
            continue
        if beta != beta:
            continue
        cols.append(j)
        betas.append(beta)
    if not cols:
        return None
    pred = float(intercept) + Z[:, cols] @ np.asarray(betas, dtype=np.float64)
    return np.round(pred, 6)


def _write_head(items: Sequence[dict], codes: Sequence[str], pred: Any, fields: Sequence[str], score_codes: Optional[Collection[str]]) -> None:
    allow = None
    if score_codes is not None:
        allow = {str(c or "").strip() for c in score_codes if str(c or "").strip()}
    index = {str(c or "").strip(): i for i, c in enumerate(codes)}
    for it in items:
        code = str(it.get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            continue
        i = index.get(code)
        if i is None or i >= len(pred):
            continue
        val = float(pred[i])
        if val != val:
            continue
        for key in fields:
            it[key] = val


def _stamp_cube_head_scores(
    cube: Any,
    items: List[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
) -> None:
    """用立方矩阵点积写 ŷ_oo / ŷ_τc / ŷ_co / fuse，避免逐格 Python z-score。"""
    from core.signal.factors.sum_identity import (
        cs_batch_preds_look_collapsed,
        filter_predict_active_names,
    )

    Z = _cube_z_matrix(cube)
    names = [str(n) for n in (getattr(cube, "names", None) or [])]
    codes = [str(c or "").strip() for c in (getattr(cube, "codes", None) or [])]
    if Z is None or not names or not items:
        return

    def _refuse(head: str, pred: Any, intercept: float) -> None:
        vals = [float(v) for v in pred]
        if cs_batch_preds_look_collapsed(vals, intercept=intercept):
            raise RuntimeError(f"{head} 截面出分拒绝：批 ŷ 塌缩远离 intercept")

    try:
        from core.research.oo_ridge import load_scoring_return_model

        model, _meta = load_scoring_return_model()
    except Exception:  # noqa: BLE001
        logger.debug("cube oo model load failed", exc_info=True)
        model = None
    if model is not None and getattr(model, "coefficients", None):
        active = filter_predict_active_names(model.coefficients.keys(), model.coefficients)
        pred = _dot_head(Z, names, model.coefficients, float(model.intercept or 0.0), active)
        if pred is not None:
            _refuse("oo", pred, float(model.intercept or 0.0))
            _write_head(
                items,
                codes,
                pred,
                ("y_oo", "predicted_score", "predicted_score_oo"),
                score_codes,
            )

    try:
        from core.research.tc_ridge import load_tau_model

        doc = load_tau_model() or {}
        rm = doc.get("return_model") if isinstance(doc, dict) else {}
    except Exception:  # noqa: BLE001
        logger.debug("cube tau model load failed", exc_info=True)
        rm = {}
    if isinstance(rm, dict) and rm.get("coefficients"):
        active = filter_predict_active_names(
            rm.get("active_features") or rm["coefficients"].keys(),
            rm["coefficients"],
        )
        pred = _dot_head(Z, names, rm["coefficients"], float(rm.get("intercept") or 0.0), active)
        if pred is not None:
            _refuse("tau", pred, float(rm.get("intercept") or 0.0))
            _write_head(
                items,
                codes,
                pred,
                ("y_τc", "predicted_score_τc", "y_tau_raw"),
                score_codes,
            )

    try:
        from core.research.co_ridge import load_co_model

        doc = load_co_model() or {}
        rm = doc.get("return_model") if isinstance(doc, dict) else {}
    except Exception:  # noqa: BLE001
        logger.debug("cube co model load failed", exc_info=True)
        rm = {}
    if isinstance(rm, dict) and rm.get("coefficients"):
        active = filter_predict_active_names(
            rm.get("active_features") or rm["coefficients"].keys(),
            rm["coefficients"],
        )
        pred = _dot_head(Z, names, rm["coefficients"], float(rm.get("intercept") or 0.0), active)
        if pred is not None:
            _refuse("co", pred, float(rm.get("intercept") or 0.0))
            _write_head(
                items,
                codes,
                pred,
                ("y_co", "predicted_score_co", "predicted_score_on"),
                score_codes,
            )

    try:
        from core.research.feature_standardize import is_cross_section_zscore
        from core.research.horizon_ridge import _doc_backend, load_ridge_model, write_y_hat

        for head in ("t30", "t45", "t60", "t75", "t90"):
            doc = load_ridge_model(head)
            if not doc or not is_cross_section_zscore(doc) or _doc_backend(doc) == "tree":
                continue
            rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
            coefs = rm.get("coefficients") if isinstance(rm, dict) else None
            if not isinstance(coefs, dict) or not coefs:
                continue
            active = filter_predict_active_names(
                rm.get("active_features") or coefs.keys(),
                coefs,
            )
            intercept = float(rm.get("intercept") or 0.0)
            pred = _dot_head(Z, names, coefs, intercept, active)
            if pred is None:
                continue
            _refuse(head, pred, intercept)
            allow = None
            if score_codes is not None:
                allow = {str(c or "").strip() for c in score_codes if str(c or "").strip()}
            index = {code: i for i, code in enumerate(codes)}
            for it in items:
                code = str(it.get("stock_code") or "").strip()
                if allow is not None and code not in allow:
                    continue
                i = index.get(code)
                if i is None:
                    continue
                write_y_hat(head, it, float(pred[i]))
    except Exception:  # noqa: BLE001
        logger.debug("cube horizon heads skipped", exc_info=True)

    try:
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg
        from core.signal.yhat_windows import stamp_window_scores

        cfg = get_path_matrix_cfg()
        for it in items:
            stamped = stamp_window_scores(it, cfg)
            for key in ("y_oo_fuse", "y_oo", "y_τc", "y_co"):
                if stamped.get(key) is not None:
                    it[key] = stamped[key]
    except Exception:  # noqa: BLE001
        logger.debug("cube fuse stamp failed", exc_info=True)


def _try_score_pool_from_cube(
    codes: Sequence[str],
    *,
    clock: str,
    cube_codes: Optional[Sequence[str]],
    score_codes: Optional[Collection[str]],
) -> Optional[Tuple[List[dict], List[dict]]]:
    """宇宙立方已在盘上时直接出分。没有立方返回 None，调用方再走 L1。"""
    from core.research.day_cube import load_day_cube, snap_clock_to_disk
    from core.signal.session_pit import oo_cycle_date

    lookup = [str(c or "").strip() for c in (cube_codes or []) if str(c or "").strip()]
    if len(lookup) < 2:
        lookup = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if len(lookup) < 2:
        return None
    clock_use = snap_clock_to_disk(lookup, clock)
    if clock_use != clock:
        logger.info("lake score clock %s -> %s (latest cube)", clock, clock_use)
    cycle_day = str(oo_cycle_date() or "")[:10]
    for day in _lake_score_day_candidates(cycle_day):
        cube = load_day_cube(day, lookup, clock=clock_use)
        if cube is None:
            continue
        scored_all = _items_from_day_cube(cube, day)
        if len(scored_all) < 2:
            continue
        if cycle_day and day != cycle_day:
            for it in scored_all:
                it["lake_score_fallback_from"] = cycle_day
        _stamp_cube_head_scores(cube, scored_all, score_codes=score_codes)
        want = [c for c in codes if c]
        want_set = set(want)
        scored = (
            [it for it in scored_all if it.get("stock_code") in want_set]
            if want_set
            else list(scored_all)
        )
        have = {str(it.get("stock_code") or "") for it in scored}
        rejected = [
            {"stock_code": c, "reason": f"L2 缺 {day}"} for c in want if c not in have
        ]
        logger.info(
            "lake score from cube day=%s clock=%s n=%s rejected=%s",
            day,
            clock_use,
            len(scored),
            len(rejected),
        )
        return scored, rejected
    return None


def _score_pool_from_lake(
    pool: Sequence[str],
    *,
    use_minute_tau: Optional[bool] = None,
    score_codes: Optional[Collection[str]] = None,
    tau_clock: Optional[str] = None,
) -> Tuple[List[dict], List[dict]]:
    """调仓出分。宇宙立方已落盘时用当日 ``X_raw``，不再按票打开 L1。

    没有立方时才读 ŷ_oo 周期日 L1；截面立方在则用其 μ/σ，只缺立方时用池内 μ/σ。

    日界与 ``oo_cycle_date`` 对齐。盘中日线/open 湖常仍停在上一交易日
    （``expected_latest_daily_bar_date`` 15:05 前不滚今日），此时回退读昨收
    周期日。观察池变更导致 open 立方哈希对不上时，不把数据中心 ŷ 列整表清空。
    """
    from core.research.batch_score import ensure_day_cube_for_items
    from core.research.feature_lake import load_day_rows
    from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, family_for_tc
    from core.research.lake_replay import assemble_item_from_lake_rows
    from core.research.materialize.consumer import MaterializeRequired
    from core.research.materialize.coverage import read_cube_codes
    from core.signal.cross_section_rescore import rescore_fitted_cross_section

    codes = [str(c or "").strip() for c in pool if str(c or "").strip()]
    clock = _consumer_score_clock(use_minute_tau, tau_clock)
    cube_codes = read_cube_codes()
    from_cube = _try_score_pool_from_cube(
        codes,
        clock=clock,
        cube_codes=cube_codes,
        score_codes=score_codes,
    )
    if from_cube is not None:
        return from_cube
    fam_tc = family_for_tc(tau_hm="open" if clock == "open" else clock)
    families = [FAMILY_OO, fam_tc, FAMILY_CO]
    day, report, fallback_from = _resolve_lake_score_day(
        codes,
        families=families,
        clock=clock,
        cube_codes=cube_codes,
    )
    l2_gap = bool(report.get("l2_gap_only"))
    if not report.get("ok") and not l2_gap:
        raise MaterializeRequired(report)

    oo_m = load_day_rows(FAMILY_OO, codes, day) or {}
    tc_m = load_day_rows(fam_tc, codes, day) or {}
    co_m = load_day_rows(FAMILY_CO, codes, day) or {}
    scored: List[dict] = []
    rejected: List[dict] = []
    for code in codes:
        item = assemble_item_from_lake_rows(
            code,
            day,
            oo=oo_m.get(code),
            tc=tc_m.get(code),
            co=co_m.get(code),
        )
        if item is None:
            rejected.append({"stock_code": code, "reason": f"L1 缺 {day}"})
            continue
        item["lake_score_day"] = day
        if fallback_from:
            item["lake_score_fallback_from"] = fallback_from
        scored.append(item)
    if len(scored) >= 2:
        cube = ensure_day_cube_for_items(
            scored,
            date=day,
            clock=clock,
            persist=False,
            # 与 assess_coverage 同键：完整观察池，不是当日有行子集
            universe_codes=cube_codes or codes,
        )
        if cube is None and l2_gap:
            logger.info(
                "lake score cube gap day=%s clock=%s n=%s; μ/σ from L1 pool",
                day,
                clock,
                len(scored),
            )
            rescore_fitted_cross_section(
                scored, score_codes=score_codes, day_cube=None
            )
            return scored, rejected
        if cube is None:
            hint = "分钟线物化" if clock != "open" else "日线物化"
            raise MaterializeRequired(
                {
                    "error_code": "materialize_required",
                    "error": f"请先{hint}：{day} {clock} 截面立方缺失",
                    "layer": "l2",
                    "hint": hint,
                    "clocks": [clock],
                    "families": families,
                    "cycle_day": fallback_from or day,
                    "tried_days": [day],
                }
            )
        rescore_fitted_cross_section(scored, score_codes=score_codes, day_cube=cube)
    return scored, rejected


def _score_pool(
    codes: Sequence[str],
    *,
    horizon_days: int = 1,
    offline_only: bool = True,
    use_minute_tau: Optional[bool] = None,
    score_codes: Optional[Collection[str]] = None,
    tau_clock: Optional[str] = None,
) -> Tuple[List[dict], List[dict]]:
    """观察池并行 score_stock。默认仅本地缓存；``offline_only=False`` 可补远端。

    ``use_minute_tau=False``：开盘 Z，不拉当天 5m。
    ``score_codes`` 给定时，截面 z 用整池特征，ŷ 只写允许档。
    产品调仓打开消费开关后改为只读 L1/L2。
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.research.materialize.consumer import MaterializeRequired, consumer_read_only
    from core.signal_service import score_one

    scored: List[dict] = []
    rejected: List[dict] = []
    pool = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if not pool:
        return scored, rejected
    if consumer_read_only():
        return _score_pool_from_lake(
            pool,
            use_minute_tau=use_minute_tau,
            score_codes=score_codes,
            tau_clock=tau_clock,
        )

    use_offline = bool(offline_only)
    t0 = time.perf_counter()
    try:
        from core.t0.score_policy import (
            build_tau_pool_watching_holdings,
            seed_tau_cross_section_pool,
            set_t0_cs_universe_codes,
            t0_cs_universe_codes,
        )

        uni = t0_cs_universe_codes(*list(pool))
        set_t0_cs_universe_codes(uni)
        seed_tau_cross_section_pool(
            build_tau_pool_watching_holdings(extra_codes=pool)
        )
    except Exception:  # noqa: BLE001
        logger.debug("watching_matrix tau CS seed failed", exc_info=True)
    try:
        from core.signal.live_features import fetch_live_index_bars

        fetch_live_index_bars(market="CN", limit=75, offline_only=use_offline)
    except Exception:  # noqa: BLE001
        logger.debug("warm index bars failed", exc_info=True)

    # batch 截面开启时：单票只抽特征，ŷ 留给池级 DayCube 单遍 predict
    defer_predict = False
    try:
        from core.research.batch_score import enabled as batch_score_enabled

        defer_predict = bool(batch_score_enabled())
    except Exception:  # noqa: BLE001
        defer_predict = False

    def _score_one(code: str) -> Tuple[str, Any]:
        try:
            result = score_one(
                code,
                horizon_days=max(1, int(horizon_days or 1)),
                skip_fundamentals=True,
                skip_sentiment=True,
                offline_only=use_offline,
                quote_timeout=5.0 if use_offline else 8.0,
                use_minute_tau=use_minute_tau,
                defer_cs_predict=defer_predict,
            )
        except Exception as exc:  # noqa: BLE001
            return code, {"success": False, "stock_code": code, "error": str(exc)[:120]}
        return code, result

    workers = max(1, min(8, len(pool)))
    by_code: Dict[str, Any] = {}
    from core.research.materialize.consumer import (
        bind_materialize_writer,
        context_initargs,
    )

    with ThreadPoolExecutor(
        max_workers=workers,
        initializer=bind_materialize_writer,
        initargs=context_initargs(),
    ) as ex:
        futs = {ex.submit(_score_one, c): c for c in pool}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                _, result = fut.result()
                by_code[code] = result
            except Exception as exc:  # noqa: BLE001
                logger.debug("score future failed for %s", code, exc_info=True)
                by_code[code] = {
                    "success": False,
                    "stock_code": code,
                    "error": str(exc)[:120],
                }

    for code in pool:
        result = by_code.get(code)
        if not isinstance(result, dict) or result.get("success") is False:
            rejected.append(
                {
                    "stock_code": code,
                    "reason": (result or {}).get("error") or "score_stock 失败",
                }
            )
            continue
        item = result.get("signal_item") if isinstance(result.get("signal_item"), dict) else result
        if not isinstance(item, dict):
            rejected.append({"stock_code": code, "reason": "无 signal_item"})
            continue
        if item.get("hard_reject"):
            rejected.append(
                {
                    "stock_code": item.get("stock_code") or code,
                    "stock_name": item.get("stock_name"),
                    "reason": item.get("reject_reason") or "hard_reject",
                }
            )
            continue
        if item.get("stock_code") is None:
            item["stock_code"] = code
        scored.append(item)

    try:
        from core.research.batch_score import (
            enabled as batch_score_enabled,
            rescore_fitted_cross_section_batched,
        )
        from core.signal.cross_section_rescore import rescore_fitted_cross_section

        if batch_score_enabled():
            clock = "open"
            if use_minute_tau:
                try:
                    from datetime import datetime

                    clock = f"tau_{datetime.now().strftime('%H%M')}"
                except Exception:  # noqa: BLE001
                    clock = "tau_live"
            # L2 键用完整观察池；score_codes 可能是分档子集
            uh = None
            try:
                from core.research.materialize.coverage import read_cube_codes

                uh = read_cube_codes() or None
            except Exception:  # noqa: BLE001
                uh = None
            rescore_fitted_cross_section_batched(
                scored,
                score_codes=score_codes,
                clock=clock,
                persist_cube=False,
                universe_codes=uh or list(pool),
            )
        else:
            rescore_fitted_cross_section(scored, score_codes=score_codes)
    except MaterializeRequired:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("watching_matrix cross-section rescore failed")
        raise RuntimeError(
            f"watching_matrix 截面 rescore 失败（禁止 raw×β）: {exc}"
        ) from exc

    logger.info(
        "watching_matrix score_pool n=%s scored=%s rejected=%s workers=%s "
        "elapsed=%.1fs offline_only=%s defer_cs=%s",
        len(pool),
        len(scored),
        len(rejected),
        workers,
        time.perf_counter() - t0,
        int(use_offline),
        int(defer_predict),
    )
    return scored, rejected


def _live_quote_dict(code: str) -> Optional[dict]:
    try:
        from core.data.facade import get_quote

        q = get_quote(code) or {}
        return q if isinstance(q, dict) else None
    except Exception:  # noqa: BLE001
        logger.debug("live quote failed for %s", code, exc_info=True)
        return None


def _px_from_quote_last(q: dict) -> Optional[float]:
    px = _f(q.get("price_raw"))
    if px is None:
        px = _f(q.get("price"))
    return float(px) if px is not None and px > 0 else None


def _px_from_quote_open(q: dict) -> Optional[float]:
    px = _f(q.get("open_raw"))
    if px is None:
        px = _f(q.get("open"))
    return float(px) if px is not None and px > 0 else None


def _px_from_quote_prev(q: dict) -> Optional[float]:
    for key in ("prev_close", "pre_close", "yesterday_close"):
        px = _f(q.get(key))
        if px is not None and px > 0:
            return float(px)
    return None


def _live_fill_px(code: str) -> Optional[float]:
    """实时成交价：现价；集合竞价未出、现价仍停在昨收则用今开。不退昨收。"""
    q = _live_quote_dict(code)
    if not q:
        return None
    last = _px_from_quote_last(q)
    opx = _px_from_quote_open(q)
    prev = _px_from_quote_prev(q)
    if (
        last is not None
        and prev is not None
        and opx is not None
        and abs(last - prev) < 1e-9
        and abs(opx - prev) > 1e-9
    ):
        return float(opx)
    if last is not None:
        return float(last)
    if opx is not None:
        return float(opx)
    return None


def _bar_close_px(code: str) -> Optional[float]:
    try:
        from core.data.facade import get_bars

        pack = get_bars(
            code,
            limit=5,
            offline_only=True,
            reject_quote_fallback=True,
        ) or {}
        bars = list(pack.get("bars") or [])
        if bars:
            px = _f((bars[-1] or {}).get("close"))
            if px is not None and px > 0:
                return float(px)
    except Exception:  # noqa: BLE001
        logger.debug("cached bar price failed for %s", code, exc_info=True)
    return None


def _quote_px(code: str, *, offline_only: bool = True) -> Optional[float]:
    """调仓成交价。

    ``offline_only=True``：本地日线末收（盘中即昨收，仅离线预演）。
    ``offline_only=False``：实时现价，缺则今开。不把昨收写成成交/成本。
    """
    if not offline_only:
        return _live_fill_px(code)
    return _bar_close_px(code)


def _quote_open_px(code: str, *, offline_only: bool = True) -> Optional[float]:
    """当日开盘：仅实时行情。offline 预演不扣 remaining，避免用昨收/昨开误映射。"""
    if offline_only:
        return None
    q = _live_quote_dict(code)
    return _px_from_quote_open(q) if q else None


_SCORE_PASSTHROUGH_KEYS = (
    "predicted_score",
    "predicted_score_eod",
    "predicted_score_eod_rem",
    "predicted_score_tau",
    "predicted_score_co",
    "y_oo",
    "y_co",
    "y_τc",
    *AUX_YHAT_KEYS,
    "y_oo_fuse",
    "residual",
    "y_oo_fuse_score",
    "gap_pct",
    "open_gap_pct",
    "change_pct",
    "prev_close",
    "last_price",
    "hard_reject",
    "reject_reason",
    "sector",
)


def _score_fields_for_report(item: dict, scores: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """透传 signal_item 打分字段，供前端 resolve*Score 解析表列。"""
    out: Dict[str, Any] = {}
    if isinstance(item, dict):
        for k in _SCORE_PASSTHROUGH_KEYS:
            if k in item and item.get(k) is not None:
                out[k] = item.get(k)
    yo = scores.get("y_co")
    if yo is not None:
        out["y_co"] = yo
        out.setdefault("predicted_score_co", yo)
    return out


def _row_score_payload(
    item: Optional[dict],
    src: Optional[dict] = None,
    cfg: Optional[dict] = None,
    *,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
) -> Dict[str, Any]:
    """表列用 ŷ_oo / ŷ_τc / ŷ_co；y_oo_fuse（UI fuse）现算。"""
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item
    from core.paper.rebalance.rank_lots import aux_yhat_fields, y_oo_fuse_pct_of
    from core.signal.yhat_windows import pick_y_co

    item = item if isinstance(item, dict) else {}
    src = src if isinstance(src, dict) else {}
    sc = scores_from_rebalance_item(item, cfg)
    yo = pick_y_co(src)
    if yo is None:
        yo = pick_y_co(item)
    payload = _score_fields_for_report(item, {"y_co": yo})
    for k in ("y_oo", "y_τc", "y_oo_fuse", "residual"):
        if sc.get(k) is not None:
            payload[k] = sc.get(k)
    o = _f(open_px)
    if o is None or o <= 0:
        o = _f(item.get("day_open"))
    p = _f(price_tau)
    if p is None or p <= 0:
        p = _f(src.get("price"))
    yf = y_oo_fuse_pct_of(item, cfg, open_px=o, price_tau=p)
    if yf is None:
        yf = y_oo_fuse_pct_of(src, cfg, open_px=o, price_tau=p)
    if yf is not None:
        payload["y_oo_fuse"] = yf
    rs = src.get("y_oo_fuse_score")
    if rs is not None:
        payload["y_oo_fuse_score"] = rs
    payload.update(aux_yhat_fields(src))
    payload.update(aux_yhat_fields(item))
    payload.update(tip_explain_fields(src))
    payload.update(tip_explain_fields(item))
    return payload


def _apply_one_leg(
    paper: dict,
    leg: dict,
    *,
    as_of: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """按计划价（含成本模型滑点）落地一笔买卖。先卖后买由调用方保证。"""
    from core.paper.costs import (
        annotate_trade,
        apply_fill_price,
        calc_trade_fees,
        cost_params,
        resolve_cost_model,
    )
    from core.paper.ledger import ORIGIN_STRATEGY, _now_iso, merge_origin
    from core.paper.sizing import _lot_shares as lot_sh

    side = str(leg.get("side") or "").strip().lower()
    code = str(leg.get("stock_code") or "").strip()
    try:
        shares = float(leg.get("shares") or 0)
    except (TypeError, ValueError):
        shares = 0.0
    raw_px = _f(leg.get("price"))
    if side not in ("buy", "sell") or not code or shares <= 0 or not raw_px or raw_px <= 0:
        return None, "invalid_leg"

    model = resolve_cost_model(paper)
    params = cost_params(paper)
    fill_px = apply_fill_price(side, float(raw_px), model=model, params=params)
    cash = float(paper.get("cash") or 0)
    holdings = list(paper.get("holdings") or [])
    existing = next((h for h in holdings if str(h.get("stock_code")) == code), None)
    name = leg.get("stock_name") or (existing or {}).get("stock_name")
    note = leg.get("reason") or leg.get("matrix_action") or "watching_matrix"
    rank_fields = {
        k: leg.get(k)
        for k in (
            "y_oo_fuse",
            "y_oo",
            "y_co",
            "y_τc",
            "residual",
            "predicted_score_tau",
            "y_oo_fuse_score",
            "reason",
            *AUX_YHAT_KEYS,
            "rank_i",
            "rank_n",
            "lot_kind",
            "action",
        )
        if leg.get(k) is not None
    }
    rank_fields.update(tip_explain_fields(leg))
    if as_of:
        rank_fields["as_of"] = str(as_of)[:10]

    if side == "sell":
        have = float((existing or {}).get("shares") or 0)
        from core.paper.tplus1 import TPLUS1_LOCK_REASON, clip_sell_shares, consume_sell_lots

        sell_shares, t1_meta = clip_sell_shares(
            existing or {}, min(shares, have), as_of=as_of
        )
        sell_shares = float(lot_sh(sell_shares) or sell_shares)
        if sell_shares <= 0 or not existing:
            return None, (t1_meta.get("reason") or TPLUS1_LOCK_REASON) if have > 0 else "no_position"
        cost = float(existing.get("cost") or 0)
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees("sell", amount, model=model, params=params)
        pnl_pct = round((fill_px / cost - 1.0) * 100.0, 2) if cost else None
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": name,
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "open_date": _open_date_of(existing, as_of),
                "pnl_pct": pnl_pct,
                "score": (
                    leg.get("y_oo_fuse")
                    if leg.get("y_oo_fuse") is not None
                    else leg.get("score")
                ),
                "origin": ORIGIN_STRATEGY,
                "note": f"矩阵调仓 · {note}",
                "matrix_action": leg.get("matrix_action"),
                **rank_fields,
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        paper["cash"] = round(cash + float(fee_info.get("net_cash_delta") or 0), 2)
        consume_sell_lots(existing, sell_shares)
        if float(existing.get("shares") or 0) <= 1e-6:
            paper["holdings"] = [h for h in holdings if str(h.get("stock_code")) != code]
        paper["updated_at"] = trade["ts"]
        return trade, None

    buy_shares = float(lot_sh(shares) or shares)
    if buy_shares <= 0:
        return None, "lot"
    amount = round(buy_shares * fill_px, 2)
    fee_info = calc_trade_fees("buy", amount, model=model, params=params)
    need = amount + float(fee_info.get("fees") or 0)
    if need > cash + 1e-6:
        return None, "cash"
    try:
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        floor = float(get_rank_lot_cfg(paper).get("cash_floor") or 0.0)
    except Exception:  # noqa: BLE001
        logger.debug("cash_floor resolve failed", exc_info=True)
        floor = 0.0
    if cash - need < floor - 1e-6:
        return None, "cash_floor"
    fill_rounded = round(fill_px, 4)
    old_sh = float(existing.get("shares") or 0) if existing else 0.0
    old_cost = float(existing.get("cost") or 0) if existing else 0.0
    trade = annotate_trade(
        {
            "ts": _now_iso(),
            "side": "buy",
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "price": fill_rounded,
            "amount": amount,
            "open_date": _open_date_of(existing, as_of),
            "score": (
                leg.get("y_oo_fuse")
                if leg.get("y_oo_fuse") is not None
                else leg.get("score")
            ),
            "origin": ORIGIN_STRATEGY,
            "note": f"矩阵调仓 · {note}",
            "matrix_action": leg.get("matrix_action"),
            **rank_fields,
        },
        fee_info,
    )
    paper.setdefault("trades", []).append(trade)
    paper["cash"] = round(cash - need, 2)
    from core.paper.tplus1 import add_buy_lot, stamp_new_holding

    if existing:
        new_sh = old_sh + buy_shares
        if new_sh > 0:
            existing["cost"] = round((old_cost * old_sh + fill_px * buy_shares) / new_sh, 4)
        add_buy_lot(existing, buy_shares, ts=trade["ts"], as_of=as_of)
        existing["origin"] = merge_origin(existing.get("origin"), ORIGIN_STRATEGY)
        if name and not existing.get("stock_name"):
            existing["stock_name"] = name
    else:
        row = {
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "cost": round(fill_px, 4),
            "bought_at": trade["ts"],
            "origin": ORIGIN_STRATEGY,
        }
        stamp_new_holding(row, ts=trade["ts"], as_of=as_of)
        holdings.append(row)
        paper["holdings"] = holdings
    paper["updated_at"] = trade["ts"]
    return trade, None


def _apply_matrix_trades(
    paper: dict,
    sell_trades: Sequence[dict],
    buy_trades: Sequence[dict],
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """先卖后买落地；失败腿进 apply_skips，不中断其余。"""
    applied_sells: List[dict] = []
    applied_buys: List[dict] = []
    skips: List[dict] = []
    for leg in sell_trades:
        trade, err = _apply_one_leg(paper, leg, as_of=as_of)
        if trade is None:
            skips.append(
                {
                    "stock_code": (leg or {}).get("stock_code"),
                    "side": "sell",
                    "reason": err or "apply_failed",
                    "tplus1": bool(err and "T+1" in str(err)),
                }
            )
            continue
        applied_sells.append(trade)
    for leg in buy_trades:
        trade, err = _apply_one_leg(paper, leg, as_of=as_of)
        if trade is None:
            skips.append(
                {
                    "stock_code": (leg or {}).get("stock_code"),
                    "stock_name": (leg or {}).get("stock_name"),
                    "side": "buy",
                    "shares": (leg or {}).get("shares"),
                    "price": (leg or {}).get("price"),
                    "amount": (leg or {}).get("amount"),
                    "reason": err or "apply_failed",
                }
            )
            continue
        applied_buys.append(trade)
    return {
        "sell_trades": applied_sells,
        "buy_trades": applied_buys,
        "apply_skips": skips,
    }


def simulate_watching_matrix_preview(
    paper: dict,
    *,
    top_k: Optional[int] = None,
    dry_run: bool = True,
    offline_only: bool = True,
) -> Dict[str, Any]:
    """观察池算分 + rank_lots（y_oo_fuse 融合 ŷ_oo / ŷ_τc / ŷ_co · 已保存金额换手）。

    默认 offline 只约束 ŷ；``dry_run=False`` 落账强制现价，避免成本写成昨收。
    """
    from core.paper.ledger import mark_to_market
    from core.paper.rebalance.rank_lots import (
        ACTION_OPEN,
        ACTION_REDUCE,
        get_rank_lot_cfg,
        plan_rank_lot_day,
    )

    rl_cfg = get_rank_lot_cfg(paper, top_k=top_k)
    k = int(rl_cfg["top_k"])
    score_backend = "ridge"
    _backend_ctx = None
    try:
        from core.research.return_tree import (
            normalize_rebalance_score_backend,
            rebalance_score_backend_context,
        )

        score_backend = normalize_rebalance_score_backend(rl_cfg.get("score_backend"))
        _backend_ctx = rebalance_score_backend_context
    except Exception:  # noqa: BLE001
        logger.debug("watching_matrix score_backend failed", exc_info=True)
        score_backend = "ridge"
    rl_cfg["score_backend"] = score_backend

    held_codes = [
        str(h.get("stock_code") or "").strip()
        for h in (paper.get("holdings") or [])
        if isinstance(h, dict) and float(h.get("shares") or 0) > 0
    ]
    codes, pool_meta = _watching_codes(include_held=held_codes)
    name_by_code = (
        pool_meta.get("name_by_code")
        if isinstance(pool_meta.get("name_by_code"), dict)
        else {}
    )
    if pool_meta.get("error"):
        return {
            "success": False,
            "ok": False,
            "mode": "watching_matrix",
            "dry_run": bool(dry_run),
            "matrix_mode": True,
            "error": pool_meta.get("error"),
            "empty_reason": "empty_book",
            "confirm_supported": True,
        }
    if not codes:
        return {
            "success": False,
            "ok": False,
            "mode": "watching_matrix",
            "dry_run": bool(dry_run),
            "matrix_mode": True,
            "error": "观察池为空",
            "empty_reason": "empty_book",
            "confirm_supported": True,
        }
    _, fit_meta = _apply_universe_fit_tier_filter(codes, keep=held_codes)
    score_codes = fit_meta.get("tradable_codes")
    if not isinstance(score_codes, (list, tuple, set)):
        score_codes = None
    if isinstance(pool_meta, dict):
        pool_meta = dict(pool_meta)
        pool_meta["fit_tiers"] = fit_meta
        pool_meta["n_total"] = len(codes)
        if score_codes is not None:
            pool_meta["n_tradable"] = len(score_codes)

    # 与持仓表 / 数据中心同源：signal_config.scoring.horizon_days
    try:
        from core.signal.config import get_scoring_horizon_days

        horizon = int(get_scoring_horizon_days())
    except Exception:  # noqa: BLE001
        logger.debug("get_scoring_horizon_days failed", exc_info=True)
        horizon = 1
    use_offline = bool(offline_only)
    # ≤09:30 与回测 09:30 同口径：开盘 Z，不拉当天 5m。更晚的启动时间才吃前缀。
    use_minute_tau = live_rebalance_wants_minute_tau(rl_cfg.get("live_fill_clock"))
    # ŷ / 日K 可只读本地仓；落账必须现价，否则成本会被写成昨收
    price_offline = bool(use_offline and dry_run)
    if _backend_ctx is not None:
        with _backend_ctx(score_backend):
            scored, rejected = _score_pool(
                codes,
                horizon_days=horizon,
                offline_only=use_offline,
                use_minute_tau=use_minute_tau,
                score_codes=score_codes,
                tau_clock=rl_cfg.get("live_fill_clock"),
            )
    else:
        scored, rejected = _score_pool(
            codes,
            horizon_days=horizon,
            offline_only=use_offline,
            use_minute_tau=use_minute_tau,
            score_codes=score_codes,
            tau_clock=rl_cfg.get("live_fill_clock"),
        )
    rank_clock = _consumer_score_clock(
        use_minute_tau, rl_cfg.get("live_fill_clock")
    )
    _attach_oo_rank_scores(
        scored, score_codes=score_codes, clock=rank_clock
    )
    _attach_tc_rank_scores(
        scored, score_codes=score_codes, clock=rank_clock
    )
    try:
        save_live_rank_snapshot(scored, as_of=_live_rank_asof(scored))
    except Exception:  # noqa: BLE001
        logger.debug("save live rank snapshot failed", exc_info=True)
    try:
        from core.signal.cross_section_rescore import drop_unscored_heads

        drop_unscored_heads(scored, score_codes)
    except Exception:  # noqa: BLE001
        logger.debug("watching_matrix drop unscored heads failed", exc_info=True)
    summary = mark_to_market(paper) or {}
    equity = float(summary.get("equity") or 0) or 0.0
    cash_before = float(summary.get("cash") or paper.get("cash") or 0)
    cash = cash_before

    item_by_code = {
        str(it.get("stock_code") or "").strip(): it
        for it in scored
        if str(it.get("stock_code") or "").strip()
    }
    rejected_by = {
        str(r.get("stock_code") or "").strip(): r
        for r in (rejected or [])
        if isinstance(r, dict) and str(r.get("stock_code") or "").strip()
    }
    for code in held_codes:
        if code not in item_by_code:
            rej = rejected_by.get(code) or {}
            stub: Dict[str, Any] = {"stock_code": code}
            why = str(rej.get("reason") or "").strip()
            if why:
                stub["hard_reject"] = True
                stub["reject_reason"] = why
            item_by_code[code] = stub
        name = _resolve_report_name(
            code,
            item=item_by_code.get(code),
            paper=paper,
            name_by_code=name_by_code,
        )
        if name:
            item_by_code[code]["stock_name"] = name

    _mark_predictability_tier_exits(
        item_by_code,
        force_exit_held=list(fit_meta.get("force_exit_held") or []),
        allowed_tiers=list(fit_meta.get("allowed_tiers") or []),
    )
    _mark_predictability_tier_blocks(
        item_by_code,
        tradable_codes=fit_meta.get("tradable_codes"),
        allowed_tiers=list(fit_meta.get("allowed_tiers") or []),
    )

    prices: Dict[str, float] = {}
    opens: Dict[str, float] = {}
    for code in item_by_code:
        px = _quote_px(code, offline_only=price_offline)
        if px is not None and px > 0:
            prices[code] = float(px)
        opx = _quote_open_px(code, offline_only=price_offline)
        if opx is not None and opx > 0:
            opens[code] = float(opx)

    plan = plan_rank_lot_day(
        scored=list(item_by_code.values()),
        holdings=list(paper.get("holdings") or []),
        cash=cash_before,
        prices=prices,
        opens=opens,
        cfg=rl_cfg,
    )
    oos_excluded = sum(
        1
        for s in (plan.get("skips") or [])
        if "OOS" in str(s.get("reason") or "")
    )

    decisions: List[dict] = []
    buy_trades: List[dict] = []
    sell_trades: List[dict] = []
    report: List[dict] = []
    skips: List[dict] = list(plan.get("skips") or [])

    held_sh_of = {
        str(h.get("stock_code") or "").strip(): float(h.get("shares") or 0)
        for h in (paper.get("holdings") or [])
        if isinstance(h, dict)
    }

    def _base_row(code: str, src: Optional[dict] = None) -> dict:
        item = item_by_code.get(code) or {}
        name = _resolve_report_name(
            code, item=item, paper=paper, name_by_code=name_by_code
        )
        px = prices.get(code)
        opx = opens.get(code)
        return {
            "stock_code": code,
            "stock_name": name,
            "matrix_mode": True,
            "price": px,
            "day_open": opx,
            **_row_score_payload(
                item, src, rl_cfg, open_px=opx, price_tau=px
            ),
        }

    def _leg_row(leg: dict, *, side: str) -> dict:
        code = str(leg.get("stock_code") or "")
        act = str(leg.get("action") or "")
        held_sh = float(held_sh_of.get(code) or 0)
        shares = float(leg.get("shares") or 0)
        px = prices.get(code)
        row = {
            **_base_row(code, leg),
            "action": act,
            "reason": leg.get("reason"),
            "execute": True,
            "side": side,
            "shares": shares,
            "amount": round(shares * float(px), 2) if px else None,
        }
        if side == "buy":
            row["decision"] = "买入" if act == ACTION_OPEN else "加仓"
            row["old_shares"] = held_sh
            row["new_shares"] = held_sh + shares
            row["shares_change"] = shares
        else:
            row["decision"] = "减仓" if act == ACTION_REDUCE else "卖出"
            row["old_shares"] = held_sh
            row["new_shares"] = max(0.0, held_sh - shares)
            row["shares_change"] = -shares
        return row

    def _status_row(
        src: dict,
        *,
        decision: str,
        held_sh: float,
        new_sh: Optional[float] = None,
    ) -> dict:
        code = str(src.get("stock_code") or "")
        ns = float(held_sh if new_sh is None else new_sh)
        return {
            **_base_row(code, src),
            "action": src.get("action"),
            "reason": src.get("reason"),
            "execute": False,
            "decision": decision,
            "old_shares": held_sh,
            "new_shares": ns,
            "shares_change": ns - held_sh,
            "shares": held_sh,
        }

    for leg in plan.get("sells") or []:
        code = str(leg.get("stock_code") or "")
        px = prices.get(code)
        if px is None:
            skips.append({"stock_code": code, "reason": "无有效报价"})
            continue
        trade = {
            **leg,
            "price": px,
            "amount": round(float(leg.get("shares") or 0) * px, 2),
            "dry_run": bool(dry_run),
        }
        sell_trades.append(trade)
        report.append(_leg_row(leg, side="sell"))
        decisions.append({"stock_code": code, **leg})

    for leg in plan.get("buys") or []:
        code = str(leg.get("stock_code") or "")
        px = prices.get(code)
        if px is None:
            skips.append({"stock_code": code, "reason": "无有效报价"})
            continue
        trade = {
            **leg,
            "price": px,
            "amount": round(float(leg.get("shares") or 0) * px, 2),
            "dry_run": bool(dry_run),
        }
        buy_trades.append(trade)
        report.append(_leg_row(leg, side="buy"))
        decisions.append({"stock_code": code, "action": leg.get("action"), **leg})

    reported = {
        str(r.get("stock_code") or "").strip()
        for r in report
        if str(r.get("stock_code") or "").strip()
    }

    for sk in skips:
        code = str(sk.get("stock_code") or "").strip()
        if not code or code in reported:
            continue
        held_sh = float(held_sh_of.get(code) or 0)
        report.append(_status_row(sk, decision="跳过", held_sh=held_sh))
        reported.add(code)

    report.sort(
        key=lambda r: (
            0 if str(r.get("side") or "") == "sell" else 1,
            str(r.get("stock_code") or ""),
        )
    )

    apply_skips: List[dict] = []
    if not dry_run and (buy_trades or sell_trades):
        applied = _apply_matrix_trades(paper, sell_trades, buy_trades)
        sell_trades = list(applied.get("sell_trades") or [])
        buy_trades = list(applied.get("buy_trades") or [])
        apply_skips = list(applied.get("apply_skips") or [])
        if apply_skips:
            skips.extend(apply_skips)
        # 落账后刷新净值摘要
        summary = mark_to_market(paper) or summary
        cash = float(summary.get("cash") or paper.get("cash") or cash)

    buy_amt = sum(float(t.get("amount") or 0) for t in buy_trades)
    sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
    empty = None
    empty_detail = None
    floor_val = _f(rl_cfg.get("cash_floor")) or 0.0
    cash_skips = [
        s
        for s in skips
        if "地板" in str(s.get("reason") or "") or "现金不足" in str(s.get("reason") or "")
    ]
    if not buy_trades and not sell_trades:
        if cash_skips or (floor_val > 0 and cash_before + 1e-6 < floor_val):
            empty = "cash_below_floor"
            empty_detail = (
                f"现金 {cash_before:,.0f} < 地板 {floor_val:,.0f}"
                if floor_val > 0
                else f"现金 {cash_before:,.0f} 不足买入"
            )
        elif not scored and not held_codes:
            empty = "empty_book"
        else:
            empty = "no_executable_changes"

    by_action: Dict[str, int] = {}
    for d in decisions:
        a = str(d.get("action") or "")
        by_action[a] = by_action.get(a, 0) + 1

    note = (
        "观察池 + rank_lots 预演（未写账）"
        if dry_run
        else "观察池 + rank_lots 落账"
    )
    strategy_id = paper.get("strategy_id")
    strategy_label = None
    if strategy_id:
        try:
            from core.strategy import get_strategy_spec

            strategy_label = get_strategy_spec(str(strategy_id)).get("label")
        except Exception:  # noqa: BLE001
            logger.debug("strategy_label resolve failed", exc_info=True)
    return {
        "success": True,
        "ok": True,
        "mode": "watching_matrix",
        "dry_run": bool(dry_run),
        "matrix_mode": True,
        "offline_only": use_offline,
        "top_k": k,
        "min_score": rl_cfg.get("fuse_enter"),
        "fuse_enter": rl_cfg.get("fuse_enter"),
        "fuse_enter_pct": round(float(rl_cfg.get("fuse_enter") or 0) * 100.0, 4),
        "observation_pool_count": len(codes),
        "scored_count": len(scored),
        "rejected": rejected[:40],
        "rejected_count": len(rejected),
        "pool_meta": pool_meta,
        "buy_trades": buy_trades,
        "sell_trades": sell_trades,
        "rebalance_report": report,
        "risk_budget_skips": skips,
        "apply_skips": apply_skips,
        "summary": summary,
        "cash_impact": {
            "buy_amount": round(buy_amt, 2),
            "sell_amount": round(sell_amt, 2),
            "net_cash": round(sell_amt - buy_amt, 2),
            "cash_before": cash_before,
            "cash_after": float(paper.get("cash") or cash) if not dry_run else None,
            "cash_floor": rl_cfg.get("cash_floor"),
            "turnover_pct": round(
                (buy_amt + sell_amt) / equity * 100.0, 2
            )
            if equity > 0
            else None,
        },
        "empty_reason": empty,
        "empty_detail": empty_detail,
        "path_matrix": {
            "enabled": True,
            "mode": "rank_lots",
            "oos_excluded": oos_excluded,
            "cfg": {
                "fuse_enter": rl_cfg.get("fuse_enter"),
                "fuse_strong": rl_cfg.get("fuse_strong"),
                "cash_floor": rl_cfg.get("cash_floor"),
                "holdings_mv_cap": rl_cfg.get("holdings_mv_cap"),
                "stock_mv_cap": rl_cfg.get("stock_mv_cap"),
                "lot_base_amount": rl_cfg.get("lot_base_amount"),
                "lot_strong_amount": rl_cfg.get("lot_strong_amount"),
                "fusion_w_oo": rl_cfg.get("fusion_w_oo"),
                "fusion_w_oc": rl_cfg.get("fusion_w_oc"),
                "fusion_w_co": rl_cfg.get("fusion_w_co"),
            },
            "by_action": by_action,
        },
        "note": note,
        "strategy_id": strategy_id,
        "strategy_label": strategy_label,
        "ops_report": {
            "strategy_id": strategy_id,
            "strategy_label": strategy_label,
            "note": (
                "watching_matrix 预演"
                if dry_run
                else "watching_matrix 落账"
            ),
            "matrix_by_action": by_action,
            "matrix_mode": "rank_lots",
        },
        "confirm_supported": True,
    }


__all__ = ["simulate_watching_matrix_preview"]
