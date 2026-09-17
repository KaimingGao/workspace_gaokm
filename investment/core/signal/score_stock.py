"""单票短线评分（与 SignalEngine 共用逻辑）。"""


import logging
import threading
from typing import Any, Dict, Optional

from core.data.facade import DEFAULT_ADJUST_POLICY, allows_production_score, infer_adjust
from core.ports.market import (
    bars_from_quote_fallback,
)
from core.signal.config import load_signal_config
from core.signal.scorer import score_bars
from core.store import assess_quality

logger = logging.getLogger(__name__)


def _call_with_timeout(func, timeout, *args, **kwargs):
    """带超时的函数调用（仅用于无 AkShare 锁的路径，如腾讯行情）。

    日线等 AkShare 路径请用 ``_fetch_bars_isolated``，避免超时后线程仍占全局锁。
    """
    import socket

    result = [None]
    exception = [None]
    event = threading.Event()

    def worker():
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            result[0] = func(*args, **kwargs)
        except Exception as e:
            logger.exception('unexpected error in worker')
            exception[0] = e
        finally:
            socket.setdefaulttimeout(old_timeout)
            event.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    event.wait(timeout=timeout + 2)

    if exception[0]:
        raise exception[0]
    if not event.is_set():
        raise TimeoutError(f"操作超时（{timeout}秒）")
    return result[0]


def _fetch_bars_isolated(
    stock_code: str,
    *,
    limit: int = 40,
    timeout: float = 10.0,
    offline_only: bool = False,
):
    """评分日线：主进程只读缓存；不足时经 DataService 进程池补远端。

    ``offline_only=True``：仅本地缓存（研究枢纽强更写入），不打远端——预演调仓用。
    不在主进程直调 AkShare（避免超时后仍占 ak_lock）。
    """
    raw = str(stock_code or "").strip()
    if not raw:
        return [], "empty"

    need = min(15, int(limit or 40))
    bars: list = []
    src = "empty"
    try:
        from core.data.facade import get_bars

        pack = get_bars(
            raw,
            limit=int(limit or 40),
            offline_only=True,
            reject_quote_fallback=True,
        )
        bars = list((pack or {}).get("bars") or [])
        src = str((pack or {}).get("data_source") or "cache")
        if bars and (len(bars) >= need or offline_only):
            return bars[-int(limit) :], src
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        pass

    if offline_only:
        return (bars[-int(limit) :], src) if bars else ([], "empty")

    try:
        from core.data.service import bars_pack_worker
        from core.ports.market import batch_map

        packs = batch_map(
            bars_pack_worker,
            [raw],
            limit=int(limit or 40),
            timeout=float(timeout),
            reject_quote_fallback=True,
        )
        pack = packs[0] if packs else None
        if isinstance(pack, dict):
            bars = list(pack.get("bars") or [])
            if bars:
                src = str(pack.get("data_source") or "ak_pool")
                return bars[-int(limit) :], src
        return [], "empty"
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        return [], "empty"


def fetch_daily_bars(
    stock_code: str,
    *,
    limit: int = 40,
    timeout: float = 10.0,
    offline_only: bool = False,
    **_kwargs,
):
    """评分读日线入口（测试可 patch）；实现见 ``_fetch_bars_isolated``。"""
    return _fetch_bars_isolated(
        stock_code,
        limit=limit,
        timeout=timeout,
        offline_only=bool(offline_only),
    )


def _gated_reject_item(
    *,
    code: str,
    name: str,
    quote: dict,
    data_source: str,
    quality: dict,
    gate_reason: str,
    horizon_days: int,
) -> Dict[str, Any]:
    """P1：质量门禁拦截时的 signal_item（不调用 score_bars）。"""
    reason_map = {
        "data_quality_gate:fallback": "日线降级(quote_fallback)，不进生产评分",
        "data_quality_gate:empty": "无可用日线，不进生产评分",
        "data_quality_gate:thin": "日线质量 thin，不进生产评分",
        "factor_anomaly:missing:open_t": "今开缺失，不进生产评分",
        "factor_anomaly:missing:gap": "今开缺口缺失，不进生产评分",
        "factor_anomaly:missing:prev_close": "昨收缺失，不进生产评分",
        "factor_anomaly:missing:eod_as_of": "日线因子日缺失，不进生产评分",
        "factor_anomaly:missing:sub_scores": "β 因子全部缺测，不进生产评分",
        "factor_anomaly:pit:eod_as_of": "日线因子日错位，不进生产评分",
        "factor_anomaly:pit:eod_intraday_leak": "盘中日线因子漏入 T 日 K，不进生产评分",
        "factor_anomaly:pit:as_of_tau": "分钟 τ 日错位，不进生产评分",
        "factor_anomaly:pit:last_change_vs_gap": "缺口与涨跌口径错位，不进生产评分",
    }
    reject_reason = reason_map.get(gate_reason, f"数据质量门禁：{gate_reason}")
    signal_item = {
        "stock_code": code,
        "stock_name": name,
        "price": quote.get("price"),
        "change": quote.get("change"),
        "score": None,
        "hard_reject": True,
        "reject_reason": reject_reason,
        "factors": {},
        "reasons": [reject_reason],
        "invalidation": None,
        "sub_scores": {},
        "factor_contrib": {},
        "regime": None,
        "data_source": data_source,
        "data_quality": quality,
        "adjust": infer_adjust(data_source),
        "adjust_policy": DEFAULT_ADJUST_POLICY,
        "quality_gate": True,
        "gate_reason": gate_reason,
        "horizon_days": horizon_days,
    }
    return {
        "success": True,
        "stock_code": code,
        "stock_name": name,
        "quote": quote,
        "signal_item": signal_item,
        "scored": {
            "score": None,
            "hard_reject": True,
            "reject_reason": reject_reason,
            "quality_gate": True,
        },
        "data_source": data_source,
        "data_quality": quality,
        "quality_gate": True,
    }


def score_stock(
    stock_code: str,
    *,
    horizon_days: Optional[int] = None,
    quote: Optional[dict] = None,
    skip_fundamentals: bool = False,
    skip_sentiment: bool = False,
    bypass_quality_gate: bool = False,
    cluster_mode: Optional[str] = None,
    fetch_sector_breadth: bool = False,
    sector_gap_breadth: Optional[float] = None,
    pool_gaps: Optional[list] = None,
    sector_gap_median: Optional[float] = None,
    quote_timeout: float = 15.0,
    offline_only: bool = False,
    use_minute_tau: Optional[bool] = None,
) -> Dict[str, Any]:
    """拉行情 + 日线 + score_bars，返回 signal_item 形状 dict。

    P1：默认质量门禁 —— thin/empty/fallback 不进生产 score（hard_reject）。
    研究可传 bypass_quality_gate=True；历史回测引擎直接调 score_bars，不受影响。

    ``skip_sentiment=True``：跳过标题舆情拉取（分池刷簿必开，否则 N×超时极慢）。
    ``sector_gap_breadth``：调用方预计算的截面缺口广度（刷簿一次批量后共享）。
    ``pool_gaps``：同池缺口列表，供 theme_day 与 rem 训练同构（中位 |gap|）。
    ``sector_gap_median``：同行/截面参照缺口；有则 ``gap_vs_sector = gap − median``。
    ``fetch_sector_breadth=True``：主题缺口时再拉同伴行情算广度（默认关；
    有预计算值时不再拉；刷簿并行下勿开，否则 N×批量行情卡死）。
    ``quote_timeout``：单票行情秒数（分池刷簿宜 ≤6，避免对照长时间挂起）。
    ``offline_only=True``：日线只用本地缓存（研究枢纽强更），不补远端；
    无传入行情时用末根日线合成 quote，不打实时行情。
    ``use_minute_tau``：None=跟随 ``enable_minute_tau``（调仓因果末根 ≤10:00）；
    False=只用开盘 Z；True=强制并分钟小包（观察池 / 持仓表 / 自动调仓）。

    cluster_mode: None=读 signal_config.cluster_scoring；
    off — 不用组 β；shadow — 可算 score_cluster 对照，主分仍全局/空；
    active — 主分优先组 return_model ŷ（无模型则全局）；
    若该组 OOS 失败：主分降为全局 ŷ，再不行表列用 heuristic；
    组 ŷ 始终写 score_cluster，heuristic_score 始终保留（双轨，互不覆盖）。
    """
    if horizon_days is None:
        from core.signal.config import get_scoring_horizon_days

        horizon_days = get_scoring_horizon_days()
    horizon_days = max(1, min(int(horizon_days or 1), 10))
    raw = str(stock_code or "").strip()
    q_timeout = max(2.0, min(float(quote_timeout or 15.0), 30.0))
    use_offline = bool(offline_only)

    bars = []
    data_source = "quote_fallback"
    try:
        bars, src = fetch_daily_bars(
            raw, limit=40, timeout=10.0, offline_only=use_offline
        )
        if not bars and not use_offline:
            bars, src = fetch_daily_bars(str(raw), limit=40, timeout=10.0)
        if bars:
            data_source = src
    except TimeoutError:
        bars = []
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        bars = []

    if quote is None:
        if use_offline and bars:
            last = bars[-1] if isinstance(bars[-1], dict) else {}
            px = last.get("close")
            try:
                px_f = float(px) if px is not None else None
            except (TypeError, ValueError):
                px_f = None
            quote = {
                "success": bool(px_f and px_f > 0),
                "stock_code": raw,
                "stock_name": raw,
                "price": px_f,
                "close": px_f,
                "open": last.get("open"),
                "prev_close": (
                    bars[-2].get("close")
                    if len(bars) >= 2 and isinstance(bars[-2], dict)
                    else None
                ),
                "date": str(last.get("date") or "")[:10],
                "data_source": "bars_cache",
            }
        elif use_offline:
            return {
                "success": False,
                "stock_code": raw,
                "error": "无日线缓存（请到研究枢纽强更日 K）",
            }
        else:
            try:
                from core.data.facade import get_quote

                # 设置超时，防止行情查询卡住
                quote = _call_with_timeout(get_quote, q_timeout, raw)
            except TimeoutError:
                return {
                    "success": False,
                    "stock_code": raw,
                    "error": "行情查询超时",
                }
            except Exception as e:
                logger.exception('unexpected error in score_stock')
                return {
                    "success": False,
                    "stock_code": raw,
                    "error": f"行情查询失败: {e}",
                }

    name = quote.get("stock_name") or raw
    code = quote.get("stock_code") or raw
    if not quote.get("success"):
        return {
            "success": False,
            "stock_code": raw,
            "error": quote.get("error", "行情失败"),
        }

    if not bars and not use_offline:
        try:
            bars, src = fetch_daily_bars(str(code), limit=40, timeout=10.0)
            if bars:
                data_source = src
        except TimeoutError:
            bars = []
        except Exception:  # noqa: BLE001
            logger.debug("catch except Exception: in score_stock.py", exc_info=True)
            bars = []

    if not bars:
        bars = bars_from_quote_fallback(quote)
        data_source = "quote_fallback"

    from core.signal.session_pit import (
        prepare_eod_bars,
        quote_for_eod_score,
        resolve_minute_tau_trade_date,
        resolve_open_t,
    )

    bars_raw = list(bars or [])
    eod_bars, eod_pit = prepare_eod_bars(bars_raw, quote)
    trade_day = resolve_minute_tau_trade_date(quote, bars_raw)
    open_t_info = resolve_open_t(quote, bars_raw, trade_day=trade_day)
    if open_t_info.get("open") is None:
        try:
            from core.signal.minute_tau_feats import minutes_for_open_t

            # ŷ_oo 只读本地 5m 仓；拉网留给后面 ŷ_oc，避免每票开盘先打行情
            mins = minutes_for_open_t(str(code), fetch=False)
            if mins:
                open_t_info = resolve_open_t(
                    quote, bars_raw, trade_day=trade_day, minute_bars=mins
                )
        except Exception:  # noqa: BLE001
            logger.debug("minutes_for_open_t skipped for %s", code, exc_info=True)
    quality_bars = eod_bars if len(eod_bars) >= 2 else bars_raw
    quality = assess_quality(quality_bars or [], data_source=data_source)
    fallback = data_source in ("empty", "quote_fallback") or "fallback" in str(data_source)
    prod_ok, gate_reason = allows_production_score(
        quality_level=(quality or {}).get("level"),
        fallback=fallback,
    )
    if not bypass_quality_gate and not prod_ok:
        return _gated_reject_item(
            code=str(code),
            name=str(name),
            quote=quote,
            data_source=data_source,
            quality=quality,
            gate_reason=gate_reason,
            horizon_days=horizon_days,
        )

    from core.signal.factor_anomaly import (
        apply_factor_anomaly_to_item,
        inspect_factor_anomaly,
        merge_anomaly_reports,
    )

    factor_anomaly = inspect_factor_anomaly(
        eod_pit=eod_pit,
        open_t_info=open_t_info,
        quote=quote,
        trade_day=trade_day,
    )
    if not bypass_quality_gate and factor_anomaly.get("fatal_eod"):
        out = _gated_reject_item(
            code=str(code),
            name=str(name),
            quote=quote,
            data_source=data_source,
            quality=quality,
            gate_reason=str(factor_anomaly.get("gate_reason") or "factor_anomaly"),
            horizon_days=horizon_days,
        )
        item = out.get("signal_item") if isinstance(out.get("signal_item"), dict) else {}
        item["factor_anomaly"] = factor_anomaly
        item["eod_feature_as_of"] = eod_pit.get("eod_as_of")
        item["dual_score_window"] = eod_pit.get("dual_score_window")
        item["open_t"] = open_t_info.get("open")
        item["open_t_source"] = open_t_info.get("source")
        return out

    cfg = load_signal_config()
    fund_cfg = cfg.get("fundamentals") or {}
    fundamentals = None
    fundamentals_pit_meta: Dict[str, Any] = {}
    index_meta: Dict[str, Any] = {}
    fund_depth: Dict[str, Any] = {}
    index_bars = None
    # X0：live 财务 PIT（与 panel/OLS 同源 resolve）
    if not skip_fundamentals and fund_cfg.get("enabled", True) and fund_cfg.get(
        "fetch_on_score", True
    ):
        try:
            from core.signal.live_features import resolve_live_fundamentals

            live_fund = resolve_live_fundamentals(
                raw,
                bars=eod_bars,
                as_of=eod_pit.get("eod_as_of"),
                config=cfg,
            )
            fundamentals = (live_fund or {}).get("metrics")
            fundamentals_pit_meta = dict((live_fund or {}).get("fundamentals_pit") or {})
            if not fundamentals:
                live_fund2 = resolve_live_fundamentals(
                    str(code),
                    bars=eod_bars,
                    as_of=eod_pit.get("eod_as_of"),
                    config=cfg,
                )
                fundamentals = (live_fund2 or {}).get("metrics")
                if live_fund2 and live_fund2.get("fundamentals_pit"):
                    fundamentals_pit_meta = dict(live_fund2["fundamentals_pit"])
        except Exception as e:
            logger.exception('unexpected error in score_stock')
            fundamentals = None
            fundamentals_pit_meta = {"ok": False, "error": str(e), "mode": "error"}

    # 刷簿 / 纸面常 skip_fundamentals：本地补数（不联网）。
    # 1) valuation_em → 市值/PE/PB；2) fundamentals 快照 → ROE/增速。
    # 否则财务因子假中性 50 → z 爆炸。
    try:
        from core.valuation_em import merge_cached_valuation

        fundamentals = merge_cached_valuation(raw, fundamentals) or fundamentals
        if fundamentals is None:
            fundamentals = merge_cached_valuation(str(code), None)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        pass
    try:
        from core.fundamentals_pit import merge_local_fundamentals_snapshot

        fundamentals = merge_local_fundamentals_snapshot(raw, fundamentals) or fundamentals
        if fundamentals is None:
            fundamentals = merge_local_fundamentals_snapshot(str(code), None)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        pass

    # X1：指数日线；X5：财务深度边界
    try:
        from core.signal.live_features import (
            fetch_live_index_bars,
            infer_fundamentals_depth,
        )

        try:
            from core.ports.market import resolve_market_code

            mkt = str(
                resolve_market_code(str(code)) or resolve_market_code(raw) or "CN"
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_stock.py", exc_info=True)
            mkt = "CN"
        idx_pack = fetch_live_index_bars(
            market=mkt, limit=75, offline_only=use_offline
        )
        index_meta = {
            "ok": bool(idx_pack.get("ok")),
            "benchmark": idx_pack.get("benchmark"),
            "label": idx_pack.get("label"),
            "cached": bool(idx_pack.get("cached")),
            "reason": idx_pack.get("reason"),
            "bar_count": len(idx_pack.get("bars") or []),
        }
        if idx_pack.get("bars"):
            index_bars = idx_pack["bars"]
        fund_depth = infer_fundamentals_depth(str(code), quote=quote)
    except Exception as e:
        logger.exception('unexpected error in score_stock')
        index_meta = {"ok": False, "reason": f"index_setup_failed:{e}"}
        fund_depth = {"fundamentals_depth": "unknown", "note": str(e)}

    # 拉观察页舆情（缓存）；默认不进 score/ŷ；先验旁路见 sentiment_prior
    sentiment_ui = None
    formula_warnings: list = []
    risk_hints: list = []
    sentiment_prior: dict = {}
    sent_cfg = cfg.get("sentiment") or {}
    include_sentiment_in_score = bool(sent_cfg.get("include_in_score", False))
    if skip_sentiment:
        formula_warnings.append("sentiment_skipped_for_speed")
        sentiment_prior = {
            "success": True,
            "role": "prior",
            "mode": "off",
            "skipped": True,
            "note": "刷簿/批打分跳过舆情拉取",
        }
    else:
        try:
            from core.sentiment import fetch_stock_headlines

            sent_data = _call_with_timeout(fetch_stock_headlines, 12, raw, limit=5)
            if sent_data and sent_data.get("ok"):
                sentiment_ui = sent_data.get("sentiment")
            elif sent_data and not sent_data.get("ok"):
                formula_warnings.append(
                    f"sentiment_unavailable:{sent_data.get('error') or 'not_ok'}"
                )
            else:
                formula_warnings.append("sentiment_empty")
        except Exception as e:
            logger.exception('unexpected error in score_stock')
            formula_warnings.append(f"sentiment_fetch_failed:{e}")

        try:
            from core.sentiment_prior import build_sentiment_prior

            sentiment_prior = build_sentiment_prior(
                sentiment_ui if isinstance(sentiment_ui, dict) else None,
                config=cfg,
                stock_code=str(code),
            )
            risk_hints.extend(list(sentiment_prior.get("risk_hints") or []))
            for w in sentiment_prior.get("warnings") or []:
                if w and w not in formula_warnings:
                    formula_warnings.append(w)
        except Exception as e:
            logger.exception('unexpected error in score_stock')
            formula_warnings.append(f"sentiment_prior_failed:{e}")
            sentiment_prior = {"success": False, "role": "prior", "error": str(e)}

    # FS1 / X0 / X1：财务与指数覆盖可见
    if fundamentals_pit_meta.get("error"):
        formula_warnings.append(
            f"fundamentals_pit_failed:{fundamentals_pit_meta.get('error')}"
        )
    if fundamentals_pit_meta.get("mode") == "as_of_missing":
        formula_warnings.append("fundamentals_as_of_missing")
    if fundamentals_pit_meta.get("ann_missing"):
        formula_warnings.append("fundamentals_ann_missing")
        ann_pol = str(
            fundamentals_pit_meta.get("ann_missing_policy") or fund_cfg.get("ann_missing_policy") or ""
        ).strip().lower()
        if ann_pol in ("zero_weight", "omit") or fundamentals_pit_meta.get("mode") in (
            "ann_missing_zero_weight",
        ):
            fundamentals = None
            formula_warnings.append("fundamentals_ann_missing_zero_weight")
        if ann_pol == "hard_reject" or fundamentals_pit_meta.get("hard_reject"):
            formula_warnings.append("fundamentals_ann_missing_hard_reject")
            # 与质量门禁对齐：缺公告日硬拦
            return _gated_reject_item(
                code=str(code),
                name=str(name),
                quote=quote,
                data_source=data_source,
                quality=quality,
                gate_reason="data_quality_gate:ann_missing",
                horizon_days=horizon_days,
            )
    if fundamentals_pit_meta.get("non_pit"):
        formula_warnings.append("fundamentals_non_pit_snapshot")
    if not skip_fundamentals and fund_cfg.get("enabled", True):
        if not isinstance(fundamentals, dict) or not fundamentals:
            formula_warnings.append("fundamentals_missing")
        else:
            fund_keys = ("pe", "pb", "roe", "market_cap", "dividend_yield")
            if not any(fundamentals.get(k) is not None for k in fund_keys):
                formula_warnings.append("fundamentals_empty_metrics")
    if index_meta and not index_meta.get("ok"):
        formula_warnings.append(
            str(index_meta.get("reason") or "no_index")
        )
    if fund_depth.get("fundamentals_depth") in ("hk_shallow", "us_shallow"):
        formula_warnings.append(f"fundamentals_depth:{fund_depth.get('fundamentals_depth')}")

    # 分组 live：解析模式；组 β 仅 active 进主分（FH0）
    from core.signal.cluster.live import (
        cluster_yhat_primary_allowed,
        cluster_yhat_shadow_compute_allowed,
        get_cluster_scoring_cfg,
        lookup_code_weights,
        normalize_cluster_scoring_mode,
    )

    cs_cfg = get_cluster_scoring_cfg(cfg)
    if cluster_mode is not None:
        mode = normalize_cluster_scoring_mode(cluster_mode, enabled=True)
    else:
        mode = normalize_cluster_scoring_mode(
            cs_cfg.get("mode"), enabled=bool(cs_cfg.get("enabled"))
        )

    mapped = None
    if cluster_yhat_shadow_compute_allowed(mode):
        mapped = lookup_code_weights(str(code)) or lookup_code_weights(raw)
    weight_source = "global"
    cluster_label = None
    cluster_id = None
    cluster_version = None

    # 先解析 return_model 键，保证 ŷ β 所需 sub_scores 齐套（FS0）
    required_factor_keys: list = []
    group_model_pre = None
    global_model_pre = None
    try:
        from core.signal.cluster.live import lookup_code_return_model
        from core.signal.return_score import ReturnScoreModel
        from core.signal.return_score_store import load_return_model

        if cluster_yhat_shadow_compute_allowed(mode):
            ret_raw = (mapped or {}).get("return_model") if mapped else None
            if isinstance(ret_raw, dict):
                group_model_pre = ReturnScoreModel.from_dict(ret_raw)
            if group_model_pre is None:
                group_model_pre = lookup_code_return_model(
                    str(code)
                ) or lookup_code_return_model(raw)
        global_model_pre, _meta = load_return_model(prefer_active=True)
        for m in (group_model_pre, global_model_pre):
            if m is None:
                continue
            for k in (m.coefficients or {}).keys():
                kk = str(k).strip()
                if kk and kk not in required_factor_keys:
                    required_factor_keys.append(kk)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        required_factor_keys = []
        group_model_pre = None
        global_model_pre = None

    sentiment_for_score = sentiment_ui if include_sentiment_in_score else None
    idx_eod = index_bars
    if index_bars:
        idx_eod, _ = prepare_eod_bars(index_bars, quote)
    eod_quote = quote_for_eod_score(
        quote,
        strip_intraday_change=bool(eod_pit.get("stripped_asof_bar")),
        gap_pct=open_t_info.get("gap_pct"),
        open_t=open_t_info.get("open"),
    )
    scored = score_bars(
        eod_bars,
        horizon_days=horizon_days,
        quote=eod_quote,
        fundamentals=fundamentals,
        index_bars=idx_eod,
        config=cfg,
        sentiment=sentiment_for_score,
        required_factor_keys=required_factor_keys or None,
        # 生产打分始终算出 sub_scores / ŷ；mom3 追高只作提示，不 hard_reject 掐死入簿
        mom3_hard_reject=False,
        stock_code=str(code),
        stock_name=str(name),
    )
    if mapped and cluster_yhat_shadow_compute_allowed(mode):
        cluster_label = mapped.get("cluster_label")
        cluster_id = mapped.get("cluster_id")
        cluster_version = mapped.get("version")
        if cluster_yhat_primary_allowed(mode):
            weight_source = mapped.get("weight_source") or f"cluster:{cluster_label}"
        else:
            weight_source = "global+shadow"
    elif cluster_yhat_primary_allowed(mode):
        weight_source = "global_fallback"

    try:
        from core.portfolio_optimize import _board_for, _sector_for, load_sector_map

        smap = load_sector_map()
        sector = _sector_for(str(code), smap)
        board = _board_for(str(code))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        sector = "未分类"
        board = "其他"

    market_priors: Dict[str, Any] = {}
    try:
        from core.market.context import build_market_priors, load_market_context

        mctx = load_market_context()
        fac = scored.get("factors") or {}
        market_priors = build_market_priors(
            mctx,
            config=cfg,
            stock_code=str(code),
            sector=sector,
            rev_limit_up_count=fac.get("rev_limit_up_count"),
            rev_consecutive_limit=fac.get("rev_consecutive_limit"),
        )
        for w in market_priors.get("market_prior_warnings") or []:
            if w and w not in formula_warnings:
                formula_warnings.append(str(w))
            if w and w not in risk_hints:
                risk_hints.append(str(w))
    except Exception as e:  # noqa: BLE001
        logger.debug("market priors attach failed for %s", code, exc_info=True)
        formula_warnings.append(f"market_prior_failed:{e}")

    market_cap = None
    if isinstance(fundamentals, dict) and fundamentals.get("market_cap") is not None:
        try:
            market_cap = float(fundamentals["market_cap"])
        except (TypeError, ValueError):
            market_cap = None

    predicted_score = None
    score_global = None
    score_cluster = None
    return_model_source = None
    active_model = None
    group_model = group_model_pre
    global_model = global_model_pre
    oos_primary_blocked = False
    if cluster_yhat_primary_allowed(mode) and cluster_label:
        try:
            from core.signal.cluster.oos_labels import is_oos_failed_cluster_label

            oos_primary_blocked = is_oos_failed_cluster_label(str(cluster_label))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_stock.py", exc_info=True)
            oos_primary_blocked = False
    try:
        subs = dict(scored.get("sub_scores") or {})
        if not include_sentiment_in_score:
            subs.pop("alt_sentiment", None)

        if global_model is not None:
            score_global = global_model.predict(subs)
        if group_model is not None:
            score_cluster = group_model.predict(subs)

        if (
            cluster_yhat_primary_allowed(mode)
            and group_model is not None
            and score_cluster is not None
            and not oos_primary_blocked
        ):
            predicted_score = score_cluster
            return_model_source = "cluster_group_beta"
            active_model = group_model
        elif global_model is not None and score_global is not None:
            predicted_score = score_global
            return_model_source = (
                "oos_failed_global" if oos_primary_blocked else "global"
            )
            active_model = global_model
            if oos_primary_blocked:
                weight_source = "oos_failed_degrade"
                formula_warnings.append(
                    f"oos_failed_primary_degraded:{cluster_label}:global"
                )
        elif (
            # shadow 且无全局 return_model 产物时：用组 ŷ 顶住主分，避免全表「—」
            # OOS 失败组不走此回退（避免失败 β 进主分）
            mode == "shadow"
            and not oos_primary_blocked
            and group_model is not None
            and score_cluster is not None
        ):
            predicted_score = score_cluster
            return_model_source = "cluster_shadow_fallback"
            active_model = group_model
            formula_warnings.append(
                "no_global_return_model:shadow_uses_cluster_yhat"
            )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        predicted_score = None
        score_global = None
        score_cluster = None
        return_model_source = None
        active_model = None
        group_model = None
        global_model = None

    heuristic_score = None
    try:
        if scored.get("score") is not None and scored.get("score") != "":
            heuristic_score = float(scored.get("score"))
    except (TypeError, ValueError):
        heuristic_score = None

    # OOS 失败且无全局 ŷ：标记 heuristic 轨；表列 score 用组 ŷ%（若有），0–100 只进 heuristic_score
    if (
        oos_primary_blocked
        and predicted_score is None
        and heuristic_score is not None
        and not scored.get("hard_reject")
    ):
        return_model_source = "oos_failed_heuristic"
        active_model = None
        weight_source = "oos_failed_degrade"
        formula_warnings.append(
            f"oos_failed_primary_degraded:{cluster_label}:heuristic"
        )

    # 主分 score：始终 ŷ% 量纲。OOS heuristic 轨用组/全局 ŷ 填表列，禁止把 0–100 写入 score。
    primary_score = None
    if predicted_score is not None:
        primary_score = predicted_score
    elif (
        return_model_source == "oos_failed_heuristic"
        and not scored.get("hard_reject")
    ):
        if score_cluster is not None:
            primary_score = score_cluster
        elif score_global is not None:
            primary_score = score_global
        else:
            primary_score = None

    delta = None
    if score_cluster is not None and score_global is not None:
        delta = round(float(score_cluster) - float(score_global), 6)

    return_model_payload = None
    score_formula = ""
    score_formula_terms = None
    factor_coefficients = None
    if active_model is not None:
        try:
            from core.signal.score_view import (
                active_return_model_payload,
                build_score_formula,
            )

            return_model_payload = active_return_model_payload(
                group_model=group_model,
                global_model=global_model,
                return_model_source=return_model_source,
            )
            factor_coefficients = dict(return_model_payload.get("coefficients") or {})
            score_formula_terms = active_model.explain_prediction(
                {
                    k: v
                    for k, v in (scored.get("sub_scores") or {}).items()
                    if include_sentiment_in_score or k != "alt_sentiment"
                }
            )
            # FS3：公式始终展示 alt_sentiment 项（闸关或 β=0 时贡献记 0）
            try:
                coef_alt = factor_coefficients.get("alt_sentiment")
                alt_b = float(coef_alt) if coef_alt is not None else 0.0
            except (TypeError, ValueError):
                alt_b = 0.0
            if score_formula_terms is None:
                score_formula_terms = {
                    "intercept": float(getattr(active_model, "intercept", 0.0) or 0.0),
                    "total": float(predicted_score or 0.0),
                    "terms": [],
                }
            terms = list(score_formula_terms.get("terms") or [])
            if not any(str(t.get("key")) == "alt_sentiment" for t in terms):
                from core.signal.factors.meta.registry import factor_label

                terms.append(
                    {
                        "key": "alt_sentiment",
                        "label": factor_label("alt_sentiment"),
                        "beta": round(alt_b, 6),
                        "z": 0.0,
                        "contrib": 0.0,
                        "gated": not include_sentiment_in_score,
                        "note": (
                            "闸关·不进 ŷ"
                            if not include_sentiment_in_score
                            else ("β≈0" if abs(alt_b) < 1e-12 else "")
                        ),
                    }
                )
            # 规模缺市值：不进 sub_scores，公式里仍展示一项（贡献 0）便于对账
            scored_factors = scored.get("factors") or {}
            if (
                "size" in factor_coefficients
                and "size" not in (scored.get("sub_scores") or {})
                and not any(str(t.get("key")) == "size" for t in terms)
            ):
                from core.signal.factors.meta.registry import factor_label

                try:
                    size_b = float(factor_coefficients.get("size") or 0.0)
                except (TypeError, ValueError):
                    size_b = 0.0
                note = "缺市值·不进 ŷ"
                if scored_factors.get("size_missing"):
                    note = "缺市值·不进 ŷ"
                terms.append(
                    {
                        "key": "size",
                        "label": factor_label("size"),
                        "beta": round(size_b, 6),
                        "z": 0.0,
                        "contrib": 0.0,
                        "gated": True,
                        "note": note,
                    }
                )
            score_formula_terms = dict(score_formula_terms)
            score_formula_terms["terms"] = terms
            score_formula = build_score_formula(
                {
                    "sub_scores": {
                        k: v
                        for k, v in (scored.get("sub_scores") or {}).items()
                        if include_sentiment_in_score or k != "alt_sentiment"
                    },
                    "return_model": active_model,
                }
            )
        except Exception as e:
            logger.exception('unexpected error in score_stock')
            return_model_payload = None
            score_formula = ""
            score_formula_terms = None
            factor_coefficients = None
            formula_warnings.append(f"score_formula_failed:{e}")

    alt_beta = None
    if isinstance(factor_coefficients, dict) and "alt_sentiment" in factor_coefficients:
        try:
            alt_beta = float(factor_coefficients["alt_sentiment"])
        except (TypeError, ValueError):
            alt_beta = None

    signal_item = {
        "stock_code": code,
        "stock_name": name,
        "price": quote.get("price"),
        "change": quote.get("change"),
        "score": primary_score,
        "heuristic_score": heuristic_score,
        "predicted_score": predicted_score,
        "return_model_source": return_model_source,
        "score_scale": (
            "heuristic_0_100"
            if return_model_source == "oos_failed_heuristic"
            else ("predicted_yhat" if predicted_score is not None else None)
        ),
        "rank_mode": "predicted_score",
        "hard_reject": scored.get("hard_reject"),
        "reject_reason": scored.get("reject_reason"),
        "mom3_chase_risk": scored.get("mom3_chase_risk"),
        "factors": scored.get("factors"),
        "reasons": scored.get("reasons"),
        "invalidation": scored.get("invalidation"),
        "sub_scores": scored.get("sub_scores"),
        "factor_contrib": scored.get("factor_contrib"),
        "regime": scored.get("regime"),
        "sector": sector,
        "board": board,
        "market_cap": market_cap,
        "data_source": data_source,
        "data_quality": quality,
        "adjust": infer_adjust(data_source),
        "adjust_policy": DEFAULT_ADJUST_POLICY,
        "quality_gate": False,
        "horizon_days": horizon_days,
        "cluster_mode": mode,
        "weight_source": weight_source,
        "cluster_label": cluster_label,
        "cluster_id": cluster_id,
        "cluster_version": cluster_version,
        "score_global": score_global,
        "score_cluster": score_cluster,
        "delta_vs_global": delta,
        "return_model": return_model_payload,
        "factor_coefficients": factor_coefficients,
        "score_formula": score_formula or None,
        "score_formula_terms": score_formula_terms,
        "warnings": list(formula_warnings),
        "risk_hints": list(risk_hints),
        "sentiment_prior": sentiment_prior,
        "cross_market_prior": market_priors.get("cross_market_prior"),
        "market_sentiment_prior": market_priors.get("market_sentiment_prior"),
        "regulatory_prior": market_priors.get("regulatory_prior"),
        "ipo_drain_prior": market_priors.get("ipo_drain_prior"),
        "market_prior_active": market_priors.get("market_prior_active"),
        "market_prior_warnings": market_priors.get("market_prior_warnings"),
        "sentiment_include_in_score": include_sentiment_in_score,
        "watching_sentiment": sentiment_ui,
        "alt_sentiment_beta": alt_beta,
        "alt_sentiment_in_yhat": bool(
            include_sentiment_in_score
            and alt_beta is not None
            and abs(alt_beta) > 1e-12
            and (scored.get("sub_scores") or {}).get("alt_sentiment") is not None
        ),
        "fundamentals_pit": fundamentals_pit_meta or None,
        "index_meta": index_meta or None,
        "fundamentals_depth": fund_depth.get("fundamentals_depth"),
        "fundamentals_depth_meta": fund_depth or None,
        "feature_isomorphism_track": "X0-X5",
        "eod_feature_as_of": eod_pit.get("eod_as_of"),
        "dual_score_window": eod_pit.get("dual_score_window"),
        "open_t": open_t_info.get("open"),
        "open_t_source": open_t_info.get("source"),
        "factor_anomaly": factor_anomaly,
    }

    _fac = scored.get("factors") if isinstance(scored.get("factors"), dict) else {}
    _tail_sub = (scored.get("sub_scores") or {}).get("tail_anomaly")
    _tvr = _fac.get("tail_volume_ratio")
    _slope = _fac.get("tail_price_slope_pct")
    if _tail_sub is not None or _tvr is not None or _slope is not None:
        signal_item["tail_anomaly"] = {
            "sub_score": _tail_sub,
            "tail_volume_ratio": _tvr,
            "tail_price_slope_pct": _slope,
        }

    # R3 / A1：双层 ŷ_τ（不替换 predicted_score）
    try:
        from core.event_prior import (
            build_event_prior_from_quote,
            compute_sector_gap_breadth_live,
            get_event_prior_cfg,
        )
        from core.research.tau_ridge import load_tau_model, predict_tau_from_features
        from core.signal.dual_score import apply_tau_score_fields

        gap_v = open_t_info.get("gap_pct")
        ep_cfg = get_event_prior_cfg()
        trigger = float(ep_cfg.get("gap_trigger_pct") or 2)
        sector_breadth = None
        if sector_gap_breadth is not None:
            try:
                sector_breadth = float(sector_gap_breadth)
            except (TypeError, ValueError):
                sector_breadth = None
        # 默认不拉同伴行情：刷簿 8 路并行 × 每票 40 行情会把对照卡死数分钟。
        # 刷簿路径应预计算 sector_gap_breadth；纸面调仓另有批量路径。
        elif (
            fetch_sector_breadth
            and gap_v is not None
            and float(gap_v) >= trigger
        ):
            try:
                from core.watching.store import read_watching

                watch_codes = [
                    str(c).strip()
                    for c in ((read_watching() or {}).get("watchlist") or [])
                    if str(c).strip()
                ][:40]
                if watch_codes:
                    br = compute_sector_gap_breadth_live(
                        watch_codes,
                        gap_trigger_pct=trigger,
                        focus_code=str(code),
                        quotes={str(code): quote} if quote else None,
                    )
                    sector_breadth = br.get("breadth")
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in score_stock.py", exc_info=True)
                logger.warning("sector gap breadth failed for %s", code, exc_info=True)
        feats = {
            "gap_pct": gap_v,
            "sector_gap_breadth": sector_breadth,
            "theme_day": 0.0,
        }
        try:
            from core.research.tau_panel import (
                _finite_median,
                gap_atr_from_hist,
                gap_vs_sector_value,
                hist_bars_pit,
                mom3_pct_from_hist,
                yclose_loc_from_prev,
            )

            asof = str(open_t_info.get("trade_day") or trade_day or "")[:10]
            hist = hist_bars_pit(bars, asof_date=asof)
            feats["gap_atr"] = gap_atr_from_hist(gap_v, hist)
            open_px_z = open_t_info.get("open")
            prev_b = hist[-1] if hist else None
            if open_px_z is not None:
                try:
                    feats["yclose_loc"] = yclose_loc_from_prev(prev_b, float(open_px_z))
                except (TypeError, ValueError):
                    pass
            feats["mom3_pct"] = mom3_pct_from_hist(hist)
            try:
                from core.research.tau_panel import attach_tau_lag_features

                feats = attach_tau_lag_features(
                    feats, hist_bars=hist, asof_date=asof
                )
            except Exception:  # noqa: BLE001
                logger.debug("tau lag feats skipped for %s", code, exc_info=True)
            # 与做 T compute_scores_from_bars 同构：单票缺截面时用活跃簿宇宙
            if (
                sector_breadth is None
                or sector_gap_median is None
                or not pool_gaps
            ) and len(asof) >= 10:
                try:
                    from core.t0.score_policy import _tau_cross_section_kwargs

                    xs = _tau_cross_section_kwargs(str(code), asof)
                    if (
                        sector_breadth is None
                        and xs.get("sector_gap_breadth") is not None
                    ):
                        sector_breadth = float(xs["sector_gap_breadth"])
                        feats["sector_gap_breadth"] = sector_breadth
                    if (
                        sector_gap_median is None
                        and xs.get("sector_gap_median") is not None
                    ):
                        sector_gap_median = xs.get("sector_gap_median")
                    if not pool_gaps and xs.get("pool_gaps"):
                        pool_gaps = xs.get("pool_gaps")
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "score_stock tau XS fallback skipped for %s",
                        code,
                        exc_info=True,
                    )
            ref = sector_gap_median
            if ref is None and isinstance(pool_gaps, (list, tuple)) and pool_gaps:
                try:
                    ref = _finite_median(
                        [float(g) for g in pool_gaps if g is not None]
                    )
                except (TypeError, ValueError):
                    ref = None
            feats["gap_vs_sector"] = gap_vs_sector_value(gap_v, ref)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_stock.py", exc_info=True)
            logger.debug("tau Z extras skipped for %s", code, exc_info=True)
        try:
            from core.research.tau_theme import resolve_theme_day

            feats["theme_day"] = resolve_theme_day(
                gap_pct=gap_v,
                sector_breadth=sector_breadth,
                pool_gaps=pool_gaps if isinstance(pool_gaps, (list, tuple)) else None,
                gap_trigger_pct=trigger,
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_stock.py", exc_info=True)
            feats["theme_day"] = (
                1.0
                if (gap_v is not None and abs(float(gap_v)) >= trigger)
                else 0.0
            )
        # τ 头只吃 Z；日线 sub_scores 已在 ŷ_EOD，勿再塞进 feats

        # 分钟 τ 小包：调仓因果末根（≤10:00）；T=当前会话日（盘中≠昨收 K）
        as_of_tau_override = None
        y_spec_override = None
        try:
            from core.signal.dual_score import get_dual_score_cfg

            ds_cfg = get_dual_score_cfg()
            allow_minute = (
                bool(use_minute_tau)
                if use_minute_tau is not None
                else bool(ds_cfg.get("enable_minute_tau"))
            )
            if allow_minute:
                from core.signal.minute_tau_feats import (
                    attach_sector_ret_cs_if_missing,
                    merge_minute_tau_pack_into_feats,
                )
                from core.signal.session_pit import (
                    daily_cache_behind_tau_session,
                    recover_gap_pct_from_minute_pack,
                    resolve_minute_tau_trade_date,
                    tau_open_and_prev_close,
                )

                # 盘中 T=会话日；勿用昨收完整 K，否则 τ 停在昨天 10:00
                trade_day = resolve_minute_tau_trade_date(quote, bars)
                open_px = open_t_info.get("open")
                prev_c = open_t_info.get("prev_close")
                if open_px is None or prev_c is None:
                    open_px, prev_c = tau_open_and_prev_close(quote, bars, trade_day)
                if daily_cache_behind_tau_session(quote, bars, trade_day):
                    if open_px is not None and prev_c:
                        gap_v = round((float(open_px) / float(prev_c) - 1.0) * 100.0, 4)
                    elif open_t_info.get("gap_pct") is not None:
                        gap_v = open_t_info.get("gap_pct")
                    else:
                        gap_v = None
                    feats["gap_pct"] = gap_v
                feats, as_of_tau_override, y_spec_override = merge_minute_tau_pack_into_feats(
                    feats,
                    code=str(code),
                    trade_date=trade_day,
                    open_px=open_px,
                    prev_close=prev_c,
                    causal_rebalance=True,
                    load_cache_if_missing=True,
                    fetch_if_missing=True,
                )
                if gap_v is None:
                    recovered = recover_gap_pct_from_minute_pack(feats)
                    if recovered is not None:
                        gap_v = recovered
                        feats["gap_pct"] = gap_v
                # 与 attach_dual_score_pit / 做 T 前缀同口径：补板块开→τ，禁止 CS 缺省 z=0
                pack_hm = None
                if as_of_tau_override and "T" in str(as_of_tau_override):
                    pack_hm = str(as_of_tau_override).split("T", 1)[1][:5]
                if pack_hm:
                    feats = attach_sector_ret_cs_if_missing(
                        feats, trade_date=trade_day, tau_hm=pack_hm
                    )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_stock.py", exc_info=True)
            logger.debug("minute tau attach skipped for %s", code, exc_info=True)

        rem_model_doc = load_tau_model()
        rem_yhat = predict_tau_from_features(feats, model_doc=rem_model_doc)
        apply_tau_score_fields(
            signal_item,
            rem_yhat=rem_yhat,
            gap_pct=gap_v,
            feats=feats,
            as_of_tau=as_of_tau_override,
            y_spec_override=y_spec_override,
            rem_model_doc=rem_model_doc,
            fuse_intraday=not bool(eod_pit.get("rolled_to_next")),
        )
        try:
            from core.research.on_panel import build_on_features_from_quote_bars
            from core.research.on_ridge import load_on_model, predict_on_from_features
            from core.signal.dual_score.on import apply_on_score_fields

            on_feats = build_on_features_from_quote_bars(
                quote,
                bars,
                gap_pct=gap_v,
                ret_open_to_tau=feats.get("ret_open_to_tau"),
                stock_code=code,
                open_t=open_t_info.get("open"),
                prev_close=open_t_info.get("prev_close"),
                trade_date=open_t_info.get("trade_day"),
            )
            on_feats["sector_gap_breadth"] = sector_breadth
            on_feats["theme_day"] = feats.get("theme_day")
            on_model_doc = load_on_model()
            on_yhat = predict_on_from_features(on_feats, model_doc=on_model_doc)
            apply_on_score_fields(
                signal_item,
                on_yhat=on_yhat,
                feats=on_feats,
                on_model_doc=on_model_doc,
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_stock.py on", exc_info=True)
        try:
            from core.t0.score_policy import _attach_y_path_to_item

            _attach_y_path_to_item(
                signal_item,
                hist_bars=bars if isinstance(bars, list) else None,
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_stock.py path", exc_info=True)
        trade = signal_item.get("predicted_score_tau")
        ep = build_event_prior_from_quote(
            quote,
            bars,
            rem_yhat=trade if trade is not None else rem_yhat,
            sector_breadth=sector_breadth,
            stock_code=str(code),
        )
        signal_item["event_prior"] = ep
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_stock.py", exc_info=True)
        logger.warning("rem/event_prior attach failed for %s", code, exc_info=True)

    try:
        from core.signal.overheat_gate import annotate_item_overheat

        annotate_item_overheat(signal_item, config=cfg)
        # 同步回 scored，便于刷簿落盘
        for k in (
            "overheat",
            "overheat_scale",
            "mom_chase_risk",
            "predicted_score_overheat_scaled",
        ):
            if k in signal_item:
                scored[k] = signal_item[k]
        if scored.get("mom3_chase_risk") is None:
            scored["mom3_chase_risk"] = signal_item.get("mom_chase_risk")
    except Exception:  # noqa: BLE001
        logger.debug("overheat annotate on signal_item skipped", exc_info=True)

    late_anomaly = inspect_factor_anomaly(
        eod_pit=eod_pit,
        open_t_info=open_t_info,
        quote=quote,
        trade_day=trade_day,
        as_of_tau=signal_item.get("as_of_tau"),
        last_change=(scored.get("factors") or {}).get("last_change"),
        gap_pct=signal_item.get("gap_pct"),
        required_keys=required_factor_keys,
        sub_scores=scored.get("sub_scores"),
    )
    factor_anomaly = merge_anomaly_reports(factor_anomaly, late_anomaly)
    apply_factor_anomaly_to_item(
        signal_item, factor_anomaly, bypass=bypass_quality_gate
    )
    quality_gated = bool(signal_item.get("quality_gate"))

    return {
        "success": True,
        "stock_code": code,
        "stock_name": name,
        "quote": quote,
        "signal_item": signal_item,
        "scored": scored,
        "data_source": data_source,
        "data_quality": quality,
        "quality_gate": quality_gated,
        "cluster_mode": mode,
    }
