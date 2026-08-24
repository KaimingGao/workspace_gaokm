"""分池选股：各组 ŷ（return_model）打分 → 全局按 score 排序 → min_score 过滤 + max_names 截断。"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _prior_yhat_by_code(codes: Sequence[str]) -> Tuple[Dict[str, float], str]:
    """粗筛用先验 ŷ：优先 live 簿/scored_all，其次最近 ledger。"""
    want = {str(c).strip() for c in (codes or []) if str(c).strip()}
    scores: Dict[str, float] = {}
    axis = "none"
    if not want:
        return scores, axis

    def _ingest(rows: Sequence[Any], *, code_keys: Sequence[str], score_keys: Sequence[str]) -> None:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            code = ""
            for ck in code_keys:
                code = str(row.get(ck) or "").strip()
                if code:
                    break
            if not code or code not in want or code in scores:
                continue
            for sk in score_keys:
                raw = row.get(sk)
                if raw is None:
                    continue
                try:
                    scores[code] = float(raw)
                    break
                except (TypeError, ValueError):
                    continue

    try:
        from core.signal.cluster.live import load_active_cluster_book

        doc = load_active_cluster_book() or {}
        _ingest(
            list(doc.get("scored_all") or []) + list(doc.get("book") or []),
            code_keys=("stock_code", "code"),
            score_keys=(
                "predicted_score",
                "predicted_score_blend",
                "score",
                "predicted_score_eod",
                "yhat",
            ),
        )
        if scores:
            axis = "cluster_book_yhat"
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
        pass

    if len(scores) < max(1, len(want) // 10):
        try:
            from core.score_ledger import list_ledger_dates, load_ledger

            for d in list_ledger_dates(limit=3):
                led = load_ledger(d)
                before = len(scores)
                _ingest(
                    list(led.get("rows") or []),
                    code_keys=("code", "stock_code"),
                    score_keys=("yhat", "yhat_eod", "predicted_score"),
                )
                if len(scores) > before and axis == "none":
                    axis = "ledger_yhat"
                if len(scores) >= len(want) // 2:
                    break
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
            pass
    return scores, axis


def select_score_universe(
    codes_all: Sequence[str],
    *,
    cap: int = 240,
    unknown_quota: Optional[float] = None,
) -> Tuple[List[str], bool, str]:
    """宽宇宙截断：按先验 ŷ 降序取 cap；S2 新增 unknown 固定配额避免幸存者偏差。

    unknown_quota: 为未知票(无先验 ŷ)预留的比例，默认 0.2(20%)；若为 0 则退化为原行为。
    """
    codes = [str(c).strip() for c in (codes_all or []) if str(c).strip()]
    lim = max(1, int(cap or 1))
    if len(codes) <= lim:
        return list(codes), False, "none"
    quota_ratio = float(unknown_quota) if unknown_quota is not None else 0.2
    quota_ratio = max(0.0, min(1.0, quota_ratio))
    unknown_lim = int(round(lim * quota_ratio))
    known_lim = lim - unknown_lim
    if known_lim <= 0:
        # 极端配额全部给 unknown，仍要先填 unknown
        prior, axis = _prior_yhat_by_code(codes)
        unknown = [c for c in codes if c not in prior]
        out = unknown[:lim]
        if len(out) < lim:
            # 不够再补 known 的原序
            known = [c for c in codes if c in prior]
            out.extend(known[: lim - len(out)])
        return out[:lim], True, "unknown_full_quota_prior_yhat"

    prior, axis = _prior_yhat_by_code(codes)
    known = [(c, prior[c]) for c in codes if c in prior]
    unknown = [c for c in codes if c not in prior]
    known.sort(key=lambda x: x[1], reverse=True)
    out = [c for c, _ in known[:known_lim]]
    # S2 先扣 unknown 配额，再补 known 剩余
    unk_take = unknown[:unknown_lim]
    out.extend(unk_take)
    # known 仍有余量再补
    if len(out) < lim and len(known) > known_lim:
        tail = [c for c, _ in known[known_lim : known_lim + (lim - len(out))]]
        out.extend(tail)
    # 再不够最后填 unknown 剩余（兼容 unknown<quota 的情况）
    if len(out) < lim and len(unknown) > unknown_lim:
        tail2 = unknown[unknown_lim : unknown_lim + (lim - len(out))]
        out.extend(tail2)
    out = out[:lim]
    if not out:
        out = codes[:lim]
        return out, False, "insertion_order"
    if not known and quota_ratio == 0:
        return codes[:lim], False, "insertion_order"
    return out, True, (axis or "prior_yhat") + f"+unknown{int(quota_ratio*100)}%"


def _book_features_tau_fill(book: List[dict]) -> Dict[str, Any]:
    """簿上 features_tau 五列非空率（P0 验收）。"""
    from core.signal.dual_score import features_tau_fill_diag

    rates: List[float] = []
    missing_counts: Dict[str, int] = {}
    for row in book or []:
        ft = row.get("features_tau") if isinstance(row, dict) else None
        diag = (
            row.get("features_tau_fill")
            if isinstance(row, dict) and isinstance(row.get("features_tau_fill"), dict)
            else features_tau_fill_diag(ft)
        )
        fr = diag.get("fill_rate")
        if fr is not None:
            try:
                rates.append(float(fr))
            except (TypeError, ValueError):
                pass
        for k in diag.get("missing") or []:
            missing_counts[str(k)] = missing_counts.get(str(k), 0) + 1
    n = len(book or [])
    mean_rate = round(sum(rates) / float(len(rates)), 4) if rates else None
    full_n = sum(1 for r in rates if r >= 0.999)
    return {
        "n": n,
        "mean_fill_rate": mean_rate,
        "full_z_count": full_n,
        "full_z_rate": round(full_n / float(n), 4) if n else None,
        "missing_counts": missing_counts,
        "ok": bool(mean_rate is not None and mean_rate >= 0.8),
    }


def rank_cluster_pools(
    codes: Optional[List[str]] = None,
    *,
    horizon_days: Optional[int] = None,
    top_n_per_group: Optional[int] = None,
    max_names: Optional[int] = None,
    min_score: Optional[float] = None,
    watching_path: Optional[str] = None,
    persist_book: bool = True,
) -> Dict[str, Any]:
    """live 分池排序：组收益分后跨组按分数排序截断。

    ``top_n_per_group`` 已废弃（保留入参兼容旧 API），不再做组内 Top-N。
    """
    from core.signal.cluster.live import (
        get_cluster_scoring_cfg,
        load_active_cluster_weights,
        save_active_cluster_book,
    )
    from core.signal.cluster.oos_labels import oos_failed_cluster_labels
    from core.signal.config import get_rank_defaults, get_scoring_horizon_days, load_signal_config
    from core.signal.score_display import json_safe_number, selection_min_score
    from core.signal.score_stock import score_stock
    from core.watching.store import read_watching, refresh_watchlist

    cs = get_cluster_scoring_cfg()
    cfg = load_signal_config()
    defaults = get_rank_defaults(cfg)
    # 收益分默认：scoring.min_predicted_score（缺省 +1：ŷ<1% 不入簿）
    floor_disabled = False
    if min_score is None:
        floor = selection_min_score()
        if floor is None:
            min_score = float("-inf")
            floor_disabled = True
        else:
            min_score = float(floor)
    else:
        try:
            min_score = float(min_score)
            floor_disabled = not math.isfinite(min_score)
            if floor_disabled:
                min_score = float("-inf")
        except (TypeError, ValueError):
            min_score = float("-inf")
            floor_disabled = True
    max_n = max(1, min(int(max_names or cs.get("max_names") or 40), 80))
    if horizon_days is None:
        horizon_days = get_scoring_horizon_days(cfg)
    horizon_days = max(1, min(int(horizon_days or 1), 10))
    # 兼容旧调用方：仍回传配置值，但不参与建簿
    top_n_cfg = int(
        top_n_per_group
        if top_n_per_group is not None
        else (cs.get("top_n_per_group") or 10)
    )
    exclude_oos = True  # OOS 失败组：禁止新买入；已持仓按 heuristic 留/卖
    try:
        from core.signal.rebalance_tracks import (
            TRACK_HEURISTIC,
            TRACK_PREDICTED,
            get_rebalance_tracks_cfg,
            heuristic_score_value,
            table_yhat_score_value,
            tag_item_tracks,
        )

        tracks_cfg = get_rebalance_tracks_cfg(cfg)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
        tracks_cfg = {"enabled": False, "oos_fail_policy": "exclude"}
        TRACK_HEURISTIC = "heuristic"  # type: ignore
        TRACK_PREDICTED = "predicted"  # type: ignore

        def tag_item_tracks(item, *, oos_failed_labels=None, force_track=None):  # type: ignore
            return item

        def heuristic_score_value(item):  # type: ignore
            return None

        def table_yhat_score_value(item):  # type: ignore
            return item.get("score") if isinstance(item, dict) else None

    active = load_active_cluster_weights()
    if not active or not active.get("code_map"):
        return {
            "success": False,
            "error": "无 live 分组映射（请先 promote）",
            "task": "rank_cluster_pools",
        }

    oos_blocked_labels = set(
        oos_failed_cluster_labels(active=active) if exclude_oos else []
    )

    if codes is None:
        # 优先 live 映射码（与分组成员一致）；否则回退观察池
        mapped_codes = [
            str(c).strip()
            for c in (active.get("code_map") or {}).keys()
            if str(c).strip()
        ]
        if mapped_codes:
            codes = mapped_codes
        else:
            try:
                uni = read_watching(watching_path)
            except FileNotFoundError:
                return {"success": False, "error": "watching.json 不存在"}
            codes = list(uni.get("watchlist") or [])
            if not codes:
                refreshed = refresh_watchlist(uni, path=watching_path)
                codes = list(refreshed.get("watchlist") or [])

    codes_all = [str(c).strip() for c in (codes or []) if str(c).strip()]
    universe_n = len(codes_all)
    # 打分宇宙远宽于簿长；[:N] 按映射插入序会丢高分票。
    # 粗筛轴必须贴近生产排序（先验 ŷ），禁止用当日涨跌幅（会系统性丢掉弱动量高分票）。
    _universe_cap = 240
    universe_capped = universe_n > _universe_cap
    pre_rank_axis = "none"
    if universe_capped:
        codes, pre_ranked, pre_rank_axis = select_score_universe(
            codes_all, cap=_universe_cap
        )
    else:
        codes = list(codes_all)
        pre_ranked = False
    if not codes:
        return {"success": False, "error": "候选池为空"}

    by_label: Dict[str, List[dict]] = {}
    unmapped: List[dict] = []
    rejected: List[dict] = []
    scored_extra: List[dict] = []  # hard_reject / 未映射 / OOS 阻断，供展示
    mapped_rows: List[dict] = []
    below_min = 0
    oos_blocked_count = 0

    # 一次批量行情 → 池内共享 sector_gap_breadth（对齐 rem 面板按日广度；避免 N×同伴拉取）
    quote_cache: Dict[str, dict] = {}
    pool_breadth: Optional[float] = None
    pool_gaps_list: List[float] = []
    ref_by_code: Dict[str, Optional[float]] = {}
    try:
        from core.data.facade import batch_get_quotes
        from core.event_prior import compute_sector_gap_breadth_live, get_event_prior_cfg
        from core.research.tau_panel import sector_gap_reference_by_code

        quote_cache = dict(batch_get_quotes(codes) or {})
        trigger = float(get_event_prior_cfg().get("gap_trigger_pct") or 2.0)
        br = compute_sector_gap_breadth_live(
            codes,
            gap_trigger_pct=trigger,
            quotes=quote_cache,
            use_sector_peers=False,
        )
        if br.get("breadth") is not None:
            pool_breadth = float(br["breadth"])
        for _g in (br.get("gaps") or {}).values():
            if _g is not None:
                try:
                    pool_gaps_list.append(float(_g))
                except (TypeError, ValueError):
                    pass
        sm = {}
        try:
            from core.portfolio_optimize import load_sector_map

            sm = load_sector_map() or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
            sm = {}
        try:
            ref_by_code = sector_gap_reference_by_code(
                br.get("gaps") or {}, sector_map=sm
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
            ref_by_code = {}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
        quote_cache = {}
        pool_breadth = None
        pool_gaps_list = []
        ref_by_code = {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _score_one(raw: str) -> Dict[str, Any]:
        # 跳过完整财务 PIT（避免 N×慢拉取）；score_stock 仍会并入本地 valuation_em
        # 缓存（市值/PE 等），保证规模等因子不因 skip 打成假中性 50。
        # 刷簿跳过舆情；广度用上方池共享值，勿开 fetch_sector_breadth。
        q = quote_cache.get(raw)
        return score_stock(
            raw,
            horizon_days=horizon_days,
            cluster_mode="active",
            skip_fundamentals=True,
            skip_sentiment=True,
            quote=q if isinstance(q, dict) else None,
            sector_gap_breadth=pool_breadth,
            pool_gaps=pool_gaps_list or None,
            sector_gap_median=ref_by_code.get(raw),
            quote_timeout=5.0,
        )

    workers = max(1, min(8, len(codes)))
    scored_by_code: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_score_one, raw): raw for raw in codes}
        for fut in as_completed(futs):
            raw = futs[fut]
            try:
                scored_by_code[raw] = fut.result()
            except Exception as e:
                logger.exception('unexpected error in rank_cluster_pools')
                scored_by_code[raw] = {
                    "success": False,
                    "stock_code": raw,
                    "error": str(e),
                }

    for raw in codes:
        result = scored_by_code.get(raw) or {"success": False, "error": "no_result"}
        if not result.get("success"):
            rejected.append({"stock_code": raw, "reason": result.get("error")})
            continue
        item = result.get("signal_item") or {}
        if item.get("hard_reject"):
            rejected.append(
                {
                    "stock_code": item.get("stock_code"),
                    "reason": item.get("reject_reason"),
                    "score": item.get("score"),
                }
            )
            scored_extra.append(
                {
                    "stock_code": item.get("stock_code"),
                    "stock_name": item.get("stock_name"),
                    "score": item.get("score"),
                    "predicted_score": item.get("predicted_score"),
                    "hard_reject": True,
                    "reject_reason": item.get("reject_reason"),
                    "cluster_label": item.get("cluster_label"),
                    "weight_source": item.get("weight_source"),
                    "score_global": item.get("score_global"),
                    "delta_vs_global": item.get("delta_vs_global"),
                    "below_min_score": True,
                }
            )
            continue
        name_u = str(item.get("stock_name") or "").upper()
        if "ST" in name_u or "退" in str(item.get("stock_name") or ""):
            rejected.append(
                {
                    "stock_code": item.get("stock_code"),
                    "reason": f"ST/退市名过滤（{item.get('stock_name') or item.get('stock_code')}）",
                    "score": item.get("score"),
                }
            )
            continue
        label = item.get("cluster_label") or "_global_fallback"
        if item.get("weight_source", "").startswith("global"):
            unmapped.append(item)
            # 未映射不入选股簿（避免全局权冒充分组）；分数仍可供展示
            scored_extra.append(
                {
                    "stock_code": item.get("stock_code"),
                    "stock_name": item.get("stock_name"),
                    "score": item.get("score"),
                    "cluster_label": None,
                    "weight_source": item.get("weight_source") or "global_fallback",
                    "score_global": item.get("score_global") or item.get("score"),
                    "delta_vs_global": item.get("delta_vs_global"),
                    "below_min_score": False,
                    "unmapped": True,
                }
            )
            continue
        # P0.2：OOS 失败组 — 禁止新买入；scored_extra 保留 heuristic 供已持仓留/卖
        if exclude_oos and str(label) in oos_blocked_labels:
            oos_blocked_count += 1
            rejected.append(
                {
                    "stock_code": item.get("stock_code"),
                    "reason": f"组 {label} OOS 失败，禁止新买入",
                    "score": item.get("score"),
                    "cluster_label": str(label),
                }
            )
            try:
                hs_f = heuristic_score_value(item)
                if hs_f is not None:
                    hs_f = float(hs_f)
            except (TypeError, ValueError):
                hs_f = None
            hold_row = {
                "stock_code": item.get("stock_code"),
                "stock_name": item.get("stock_name"),
                "score": table_yhat_score_value(item),
                "predicted_score": item.get("predicted_score"),
                "heuristic_score": hs_f
                if hs_f is not None
                else item.get("heuristic_score"),
                "score_cluster": item.get("score_cluster"),
                "score_global": item.get("score_global"),
                "delta_vs_global": item.get("delta_vs_global"),
                "score_scale": item.get("score_scale") or "heuristic_0_100",
                "cluster_label": str(label),
                "cluster_id": item.get("cluster_id"),
                "weight_source": item.get("weight_source") or "oos_failed_degrade",
                "below_min_score": True,
                "oos_blocked": True,
                "return_model_source": item.get("return_model_source")
                or "oos_failed_heuristic",
                "score_formula_terms": item.get("score_formula_terms"),
                "sub_scores": item.get("sub_scores"),
                "sector": item.get("sector"),
            }
            tag_item_tracks(
                hold_row,
                oos_failed_labels=oos_blocked_labels,
                force_track=TRACK_HEURISTIC,
            )
            scored_extra.append(hold_row)
            continue
        sc = None
        try:
            from core.signal.dual_score import eod_gate_score_for_item

            sc = eod_gate_score_for_item(item)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
            sc = None
        if sc is None:
            sc = item.get("predicted_score")
        if sc is None:
            sc = item.get("score")
        try:
            sc_f = float(sc) if sc is not None else None
        except (TypeError, ValueError):
            sc_f = None
        if sc_f is None:
            continue
        sc_for_floor = sc_f
        # 入簿门槛比 raw ŷ；*_cal 仅供 tip 对照（方案 A）
        below = sc_for_floor < float(min_score)
        if below:
            below_min += 1
        row = {
            "stock_code": item.get("stock_code"),
            "stock_name": item.get("stock_name"),
            "score": sc_f,
            "predicted_score": item.get("predicted_score"),
            "predicted_score_cal": item.get("predicted_score_cal"),
            "heuristic_score": item.get("heuristic_score"),
            "score_cluster": item.get("score_cluster"),
            "score_global": item.get("score_global"),
            "delta_vs_global": item.get("delta_vs_global"),
            "score_scale": item.get("score_scale"),
            "cluster_label": str(label),
            "cluster_id": item.get("cluster_id"),
            "weight_source": item.get("weight_source"),
            "below_min_score": below,
            "return_model_source": item.get("return_model_source"),
            "score_formula_terms": item.get("score_formula_terms"),
            "sub_scores": item.get("sub_scores"),
            "formula_terms_tau": item.get("formula_terms_tau")
            or item.get("score_formula_terms_tau"),
            "score_formula_tau": item.get("score_formula_tau"),
            "sector": item.get("sector"),
            "score_rem": item.get("score_rem"),
            "predicted_score_rem": item.get("predicted_score_rem"),
            "gap_pct": item.get("gap_pct"),
            "event_prior": item.get("event_prior"),
        }
        tag_item_tracks(row, oos_failed_labels=oos_blocked_labels, force_track=TRACK_PREDICTED)
        # 冻结对账用：刷簿时 quote.open（PIT），避免事后日线复权漂移
        q_row = quote_cache.get(str(item.get("stock_code") or raw) or "")
        if isinstance(q_row, dict):
            try:
                from core.event_prior import _parse_open_price

                opx = _parse_open_price(q_row)
                if opx is not None and opx > 0:
                    row["open_price_for_tau_label"] = float(opx)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
                pass
        try:
            from core.signal.dual_score import (
                align_trade_score_fields,
                dual_score_book_fields,
                dual_track_score_fields,
            )

            row.update(dual_track_score_fields(item))
            row.update(dual_score_book_fields(item))
            # 簿主分 = raw ŷ_trade；heuristic 轨 align 会把 score 写成组/全局 ŷ%
            align_trade_score_fields(row, write_score=True)
            row.update(dual_track_score_fields(row))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
            pass
        mapped_rows.append(row)
        by_label.setdefault(str(label), []).append(row)

    # 组内榜仅供对照展示
    groups_out: List[Dict[str, Any]] = []
    for label in sorted(by_label.keys()):
        members = sorted(
            by_label[label],
            key=lambda x: (
                -float(x.get("score") or 0.0),
                str(x.get("stock_code") or ""),
            ),
        )
        ranked = []
        for i, it in enumerate(members):
            ranked.append({**it, "rank_in_group": i + 1})
        groups_out.append(
            {
                "label": label,
                "scored_count": len(ranked),
                "eligible_count": sum(
                    1 for r in ranked if not r.get("below_min_score")
                ),
                "ranking": ranked,
            }
        )

    # 选股簿：全局按 ŷ_trade 降序 → min_score 过滤（仍看 ŷ_EOD）→ max_names 截断
    try:
        from core.signal.dual_score import get_dual_score_cfg, rank_key_field, rank_key_for_item

        _dual_cfg = get_dual_score_cfg()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
        _dual_cfg = {"fusion_mode": "blend"}

        def rank_key_for_item(x, config=None):  # type: ignore
            try:
                return float(x.get("score") or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def rank_key_field(*, config=None):  # type: ignore
            return "predicted_score_blend"

    eligible = [
        dict(r) for r in mapped_rows if not r.get("below_min_score")
    ]
    # 约束感知装填：可成交过滤 · 行业名额 · τ 闸 defer/exclude（与纸面 risk 同配置）
    book_skips: List[dict] = []
    book_constraint_stats: Dict[str, Any] = {}
    try:
        from core.signal.book_constraints import (
            fill_book_with_constraints,
            get_book_constraints_cfg,
            resolve_book_risk_limits,
        )

        fill = fill_book_with_constraints(
            eligible,
            max_names=max_n,
            quotes=quote_cache,
            dual_cfg=_dual_cfg,
            risk_limits=resolve_book_risk_limits(),
            constraints=get_book_constraints_cfg(cfg),
        )
        book = list(fill.get("book") or [])
        book_skips = list(fill.get("skipped") or [])
        book_constraint_stats = dict(fill.get("stats") or {})
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
        eligible.sort(
            key=lambda x: (
                -float(
                    rank_key_for_item(x, config={"dual_score": _dual_cfg}) or 0.0
                ),
                str(x.get("stock_code") or ""),
            ),
        )
        book = eligible[:max_n]
        for i, b in enumerate(book):
            b["rank"] = i + 1

    for i, b in enumerate(book):
        b["rank"] = i + 1
        if not b.get("rank_key"):
            b["rank_key"] = (
                "heuristic_score"
                if b.get("score_track") == TRACK_HEURISTIC
                else rank_key_field(config={"dual_score": _dual_cfg})
            )

    scored_all: List[dict] = list(scored_extra) + list(mapped_rows)
    for s in book_skips:
        # 跳过票保留在 scored_all 展示（若尚未在 mapped）
        code_s = str(s.get("stock_code") or "")
        if code_s and not any(
            str(x.get("stock_code") or "") == code_s for x in scored_all
        ):
            scored_all.append({**s, "below_min_score": False, "book_skipped": True})
        else:
            for x in scored_all:
                if str(x.get("stock_code") or "") == code_s:
                    x["book_skip_reason"] = s.get("skip_reason")
                    x["book_skipped"] = True
                    break
    scored_all.sort(
        key=lambda x: (
            0 if rank_key_for_item(x, config={"dual_score": _dual_cfg}) is not None else 1,
            -(
                float(rank_key_for_item(x, config={"dual_score": _dual_cfg}) or 0.0)
            ),
            str(x.get("stock_code") or ""),
        )
    )

    n = len(book)
    w_pct = round(100.0 / n, 4) if n else 0.0
    for b in book:
        b["weight_pct"] = w_pct

    min_score_out = json_safe_number(min_score)
    path = None
    tau_shadow_path = None
    tau_shadow_meta = None
    nowcast_shadow_path = None
    nowcast_shadow_meta = None
    if persist_book:
        path = save_active_cluster_book(
            book,
            meta={
                "version": active.get("version"),
                "horizon_days": horizon_days,
                "min_score": min_score_out,
                "min_score_disabled": floor_disabled or min_score_out is None,
                "max_names": max_n,
                "mode": "cluster_score_global_rank",
                # 兼容旧 status 读取
                "top_n_per_group": top_n_cfg,
                "oos_blocked_labels": sorted(oos_blocked_labels),
                "oos_blocked_count": oos_blocked_count,
                "rebalance_tracks": {
                    "oos_fail_policy": (tracks_cfg or {}).get("oos_fail_policy"),
                    "heuristic_buy_floor": (tracks_cfg or {}).get("heuristic_buy_floor"),
                    "heuristic_hold_floor": (tracks_cfg or {}).get("heuristic_hold_floor"),
                },
                "sector_gap_breadth": pool_breadth,
                "book_constraints": book_constraint_stats,
                "book_skips": book_skips[:40],
                "book_skips_count": len(book_skips),
            },
            scored_all=scored_all,
        )
        # A2：同池按 ŷ_τ 影子簿（默认开；不进 execution）
        try:
            from core.signal.cluster.live import save_tau_shadow_cluster_book
            from core.signal.dual_score import build_tau_shadow_book, get_dual_score_cfg

            ds = get_dual_score_cfg()
            if ds.get("enable_tau_shadow_book"):
                tau_book, tau_shadow_meta = build_tau_shadow_book(
                    eligible,
                    max_names=max_n,
                    eod_book=book,
                )
                tau_shadow_meta = {
                    **tau_shadow_meta,
                    "version": active.get("version"),
                    "horizon_days": horizon_days,
                    "min_score": min_score_out,
                    "eod_book_path": path,
                }
                n_tau = len(tau_book)
                w_tau = round(100.0 / n_tau, 4) if n_tau else 0.0
                for b in tau_book:
                    b["weight_pct"] = w_tau
                tau_shadow_path = save_tau_shadow_cluster_book(
                    tau_book, meta=tau_shadow_meta
                )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
            tau_shadow_path = None
            tau_shadow_meta = None
        nowcast_shadow_path = None
        nowcast_shadow_meta = None
        try:
            from core.signal.cluster.live import save_nowcast_shadow_cluster_book
            from core.signal.dual_score import (
                build_nowcast_shadow_book,
                nowcast_shadow_alerts,
            )

            # nowcast 是表列对照分：刷簿即写影子，不依赖 nowcast.enabled / write_shadow
            nc_book, nowcast_shadow_meta = build_nowcast_shadow_book(
                eligible,
                max_names=max_n,
                eod_book=book,
            )
            nowcast_shadow_meta = {
                **nowcast_shadow_meta,
                "version": active.get("version"),
                "horizon_days": horizon_days,
                "min_score": min_score_out,
                "eod_book_path": path,
            }
            # P3-2：nowcast 影子闭环告警 — 对照决策簿(EOD/blend)，背离发告警
            try:
                _nc_alerts = nowcast_shadow_alerts(
                    nc_book,
                    nowcast_shadow_meta,
                    decision_book=book,
                )
                nowcast_shadow_meta = {
                    **nowcast_shadow_meta,
                    "alerts": _nc_alerts,
                }
                if _nc_alerts:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "nowcast shadow alerts: %s",
                        "; ".join(a.get("code", "?") for a in _nc_alerts),
                    )
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
                pass
            n_nc = len(nc_book)
            w_nc = round(100.0 / n_nc, 4) if n_nc else 0.0
            for b in nc_book:
                b["weight_pct"] = w_nc
            nowcast_shadow_path = save_nowcast_shadow_cluster_book(
                nc_book, meta=nowcast_shadow_meta
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in cluster_rank.py", exc_info=True)
            nowcast_shadow_path = None
            nowcast_shadow_meta = None

    empty_reason = None
    if not book:
        if below_min and universe_n and below_min >= universe_n or below_min and not eligible:
            empty_reason = "all_below_eod_floor"
        elif eligible and book_skips and not book:
            empty_reason = "book_constraints_empty"
        elif not mapped_rows:
            empty_reason = "no_mapped_scores"
        else:
            empty_reason = "empty_book"

    return {
        "success": True,
        "task": "rank_cluster_pools",
        "mode": "cluster_score_global_rank",
        "cross_group_rank": True,
        "cluster_version": active.get("version"),
        "top_n_per_group": top_n_cfg,  # 兼容字段；建簿已不再使用
        "max_names": max_n,
        "min_score": min_score_out,
        "min_score_disabled": floor_disabled or min_score_out is None,
        "horizon_days": horizon_days,
        "oos_blocked_labels": sorted(oos_blocked_labels),
        "oos_blocked_count": oos_blocked_count,
        "rebalance_tracks": tracks_cfg,
        "groups": groups_out,
        "book": book,
        "ranking": book,
        "name_count": len(book),
        "scored_all": scored_all,
        "unmapped_count": len(unmapped),
        "below_min_score_count": below_min,
        "empty_reason": empty_reason,
        "score_universe_n": universe_n,
        "score_universe_capped": universe_capped,
        "score_universe_pre_ranked": pre_ranked,
        "score_universe_pre_rank_axis": pre_rank_axis,
        "rejected": rejected[:20],
        "book_path": path,
        "tau_shadow_book_path": tau_shadow_path,
        "tau_shadow_meta": tau_shadow_meta,
        "nowcast_shadow_book_path": nowcast_shadow_path,
        "nowcast_shadow_meta": nowcast_shadow_meta,
        "sector_gap_breadth": pool_breadth,
        "features_tau_fill": _book_features_tau_fill(book),
        "book_constraints": book_constraint_stats,
        "book_skips": book_skips[:40],
        "book_skips_count": len(book_skips),
        "note": (
            "分池：组ŷ→剔ST→剔OOS失败组→ŷ≥min_predicted_score→约束装填"
            "（可成交/行业名额/τ defer）→max_names。"
            "刷簿前批量行情算池内 sector_gap_breadth + theme_day（与 rem 同构）。"
            "不写 signal_config.weights。另写 A2 τ 影子簿。"
        ),
    }
