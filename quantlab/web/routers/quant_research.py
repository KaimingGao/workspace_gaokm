"""量化研究台 API — factor research。"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from web import deps
from web.schemas import (
    CrossSectionRequest,
    FactorCsIcRequest,
    ExprEvalRequest,
    FactorExperimentRequest,
    FactorOlsPoolRequest,
    CoRidgeRequest,
    OoRankRequest,
    TcRankRequest,
    T30RidgeRequest,
    T45RidgeRequest,
    T60RidgeRequest,
    T75RidgeRequest,
    T90RidgeRequest,
    TauRidgeRequest,
    ThresholdSuggestRequest,
    WeightSuggestRequest,
    YhatResidualShadowRequest,
    ResearchTaskRequest,
)

router = APIRouter(tags=["quant"])

@router.get("/api/quant/factors")

def quant_factors() -> Any:
    return deps.quant.list_factors()

@router.get("/api/quant/factor-panel")

def quant_factor_panel(
    code: str = "茅台",
    with_experiment: bool = False,
    lookback: int = 120,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    try:
        return deps.quant.build_factor_panel(
            code,
            lookback=lookback,
            horizon_days=horizon_days,
            with_experiment=with_experiment,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/cross-section")

def quant_cross_section(body: CrossSectionRequest) -> Dict[str, Any]:
    try:
        return deps.quant.run_cross_section(
            codes=body.codes,
            limit=body.limit,
            min_score=body.min_score,
            horizon_days=body.horizon_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/factor-experiment")

def quant_factor_experiment(body: FactorExperimentRequest) -> Dict[str, Any]:
    try:
        return deps.quant.run_factor_experiment(
            body.code,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/factor-ols")

def quant_factor_ols(body: FactorExperimentRequest) -> Dict[str, Any]:
    try:
        return deps.quant.run_factor_ols_experiment(
            body.code,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            ridge_lambda=body.ridge_lambda,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/factor-ols-pool")

def quant_factor_ols_pool(body: FactorOlsPoolRequest) -> Dict[str, Any]:
    """研究池堆叠时序 OLS；显式触发，默认不进页自动跑。"""
    try:
        return deps.quant.run_factor_ols_pool_experiment(
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/tau-ridge")

def quant_tau_ridge(body: TauRidgeRequest) -> Dict[str, Any]:
    """ŷ_τ Ridge + 时间 OOS；可选 persist 到 live/tau_ridge_model.json。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/tau-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        theme_boost=body.theme_boost,
        persist=body.persist,
        note=body.note,
        tau_hm=body.tau_hm,
        tau_end_hm=body.tau_end_hm,
        force_promote=body.force_promote,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
        cross_section_zscore=bool(body.cross_section_zscore),
    )
    try:
        if body.persist or body.sync:
            return deps.quant.run_tau_ridge_experiment(**kwargs)
        return deps.quant.start_tau_ridge_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/yhat-residual/shadow")

def quant_yhat_residual_shadow(body: YhatResidualShadowRequest) -> Dict[str, Any]:
    """ŷ 行业残差 on/off：同截面 TopK 重叠影子对照（不写盘）。"""
    try:
        return deps.quant.run_yhat_residual_shadow(
            watching_limit=body.watching_limit,
            top_k=body.top_k,
            prefer_cluster_book=body.prefer_cluster_book,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/tau-ridge/model")

def quant_tau_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_τ 模型（主路径 tau_ridge_model.json；可读旧 rem 文件）。"""
    try:
        return deps.quant.get_tau_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/co-ridge")

def quant_co_ridge(body: CoRidgeRequest) -> Dict[str, Any]:
    """隔夜缺口 Ridge：open[T+1]/close[T]-1 + 时间 OOS；可选 persist 到 live。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/co-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        theme_boost=body.theme_boost,
        persist=body.persist,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
        cross_section_zscore=bool(body.cross_section_zscore),
    )
    try:
        if body.persist or body.sync:
            return deps.quant.run_co_ridge_experiment(**kwargs)
        return deps.quant.start_co_ridge_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/co-ridge/model")

def quant_co_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_co 模型（主路径 co_ridge_model.json；可读旧 on 文件）。"""
    try:
        return deps.quant.get_co_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/oo-rank")

def quant_oo_rank(body: OoRankRequest) -> Dict[str, Any]:
    """ŷ_oo_rank：同窗 LambdaRank+Ridge（成交 oo_rank=1..n；过入场后作买序；可选 oo_rank_max，与 τc_rank 共用）。persist 按 NDCG@K 自动选 backend 启用，不重训。"""
    try:
        return deps.quant.run_oo_rank_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            holdout_trading_days=body.holdout_trading_days,
            topk_track=body.topk_track,
            ndcg_k=body.ndcg_k,
            l2=body.l2,
            backend=body.backend,
            persist=body.persist,
            persist_role=body.persist_role,
            note=body.note,
            cross_section_zscore=bool(body.cross_section_zscore),
            zscore_universe_tier_a=bool(body.zscore_universe_tier_a),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/oo-rank/model")

def quant_oo_rank_model() -> Dict[str, Any]:
    """读取 ŷ_oo_rank 研究台：优先上次拟合草稿，否则已启用研究/执行套。"""
    try:
        return deps.quant.get_oo_rank_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/tc-rank")

def quant_tc_rank(body: TcRankRequest) -> Dict[str, Any]:
    """ŷ_τc_rank：同窗 LambdaRank+Ridge（标签 price[τ]→close[T]）。persist 按 NDCG@K 落盘；不改买序；与 oo_rank 共用 rank 入场。"""
    try:
        return deps.quant.run_tc_rank_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            holdout_trading_days=body.holdout_trading_days,
            topk_track=body.topk_track,
            ndcg_k=body.ndcg_k,
            l2=body.l2,
            backend=body.backend,
            persist=body.persist,
            persist_role=body.persist_role,
            note=body.note,
            cross_section_zscore=bool(body.cross_section_zscore),
            zscore_universe_tier_a=bool(body.zscore_universe_tier_a),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/tc-rank/model")

def quant_tc_rank_model() -> Dict[str, Any]:
    """读取 ŷ_τc_rank 研究台：优先上次拟合草稿，否则已启用研究/执行套。"""
    try:
        return deps.quant.get_tc_rank_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/research-universe")

def quant_research_universe_get(coverage: bool = False) -> Dict[str, Any]:
    """日线研究宇宙看板：名单 KPI · ∩观察池 · 可选日线覆盖。分钟暖仓不读此名单。"""
    from core.research_universe import build_research_universe_board

    try:
        return build_research_universe_board(include_coverage=bool(coverage))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/research-universe/predictability-tiers")

def quant_research_universe_predictability_tiers(
    holdout: int = 20,
    min_n: int = 20,
    head: str = "oo",
    pool: str = "watching",
    lookback: int = 120,
    a_count: int = Query(
        100, ge=1, le=2000, description="A 档：Score 序 top 数量（默认 100）"
    ),
    b_count: int = Query(
        300, ge=0, le=5000, description="B 档：接着取的数量（默认 300；其余为 C）"
    ),
    a_f1: float = Query(
        0.2,
        ge=0,
        le=100,
        description="A 档 OO_F1 门槛。0–1 为比例，大于 1 为百分数（默认 0.2）",
    ),
    tau_a_f1: float = Query(
        0.2,
        ge=0,
        le=100,
        description="A 档 TC_F1 门槛。0–1 为比例，大于 1 为百分数（默认 0.2；无研究套时不生效）",
    ),
    require_oo_r_pos: bool = Query(
        True,
        description="A 档另须 OO_R+>0（Holdout 前半 mean(y_oo|ŷ_oo>ε)；默认开）",
    ),
    require_tc_r_pos: bool = Query(
        True,
        description="A 档另须 TC_R+>0（Holdout 前半 mean(y_τc|ŷ_τc>ε)；有 ŷ_τc 研究套时生效；默认开）",
    ),
) -> Dict[str, Any]:
    """观察池分档：研究套 ŷ_oo 按票打档，落盘 last。F1/命中看 Holdout 前半；指标与等权买持看整段。

    holdout: ŷ_oo 卡片 Holdout 交易日数（分档窗=升序前半；指标窗=整段）。
    定档：Score=0.5*OO_F1+0.5*TC_F1（无 τc 研究套则 Score=OO_F1；缺一侧按 0）。
    A 档另过 a_f1 / tau_a_f1（默认 20% / 20%）；可勾选 OO_R+>0 / TC_R+>0（各独立；无 τc 研究套时 TC 不生效）；未过门槛不占 A 名额。
    过门槛者 top a_count→A、接着 b_count→B、其余→C（UI「A/B 档数量」与 F1 门槛）。
    lookback: 缺研究套现训时用的日线窗（与 ŷ_oo 训练窗对齐）；有研究套时只拉 Holdout+暖窗。
    回测天数由回测页 lookback 独立设置。
    """
    from core.research.predictability_tiers import (
        build_holdout_half_tiers,
        clamp_a_count,
        clamp_b_count,
        clamp_f1_floor,
        live_tier_status,
    )

    try:
        rep = build_holdout_half_tiers(
            holdout_n=max(2, min(int(holdout or 20), 90)),
            pool=str(pool or "watching"),
            min_n=max(1, min(int(min_n or 20), 60)),
            head=str(head or "oo"),
            lookback=max(100, min(int(lookback or 120), 1000)),
            a_count=clamp_a_count(a_count),
            b_count=clamp_b_count(b_count),
            a_f1=clamp_f1_floor(a_f1),
            tau_a_f1=clamp_f1_floor(tau_a_f1),
            require_oo_r_pos=bool(require_oo_r_pos),
            require_tc_r_pos=bool(require_tc_r_pos),
            persist=True,
        )
        if isinstance(rep, dict):
            rep["live"] = live_tier_status()
        return rep
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/research-universe/predictability-tiers/last")

def quant_research_universe_predictability_tiers_last(
    slim: bool = Query(False, description="去掉 ŷ/实现序列，只留档位摘要"),
) -> Dict[str, Any]:
    """读取上次可预测性分档影子报告 + live 闸状态（档位随历史回测勾选）。"""
    from core.research.predictability_tiers import (
        live_tier_status,
        load_predictability_tiers_last,
        predictability_tiers_last_path,
        slim_tier_rows,
    )

    try:
        last = load_predictability_tiers_last()
        live = live_tier_status()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": predictability_tiers_last_path(),
                "live": live,
                "note": "尚无分档报告；请先点「观察池分档」",
            }
        out = dict(last)
        # 旧报告可能缺 name：用观察池落盘名补全（不改磁盘）
        rows = out.get("rows")
        if isinstance(rows, list) and rows:
            from core.research.predictability_tiers import _watching_name_map

            names = _watching_name_map()
            if names:
                patched = []
                for r in rows:
                    if not isinstance(r, dict):
                        patched.append(r)
                        continue
                    row = dict(r)
                    code = str(row.get("code") or "").strip()
                    if code and not str(row.get("name") or "").strip() and names.get(code):
                        row["name"] = names[code]
                    patched.append(row)
                out["rows"] = patched
        if slim:
            out["rows"] = slim_tier_rows(out.get("rows"))
            out["slim"] = True
        out["exists"] = True
        out["path"] = predictability_tiers_last_path()
        out["live"] = live
        return out
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/research-universe/predictability-tiers/live-sync")

def quant_research_universe_predictability_tiers_live_sync(
    body: Optional[Dict[str, Any]] = Body(default=None),
) -> Dict[str, Any]:
    """历史回测「分档」勾选 → live 闸。allowed_tiers 空=关闭；非空=启用。"""
    from core.research.predictability_tiers import (
        live_tier_status,
        sync_predictability_tiers_live,
    )

    try:
        payload = body if isinstance(body, dict) else {}
        tiers = payload.get("allowed_tiers")
        if tiers is None:
            tiers = payload.get("predictability_tiers")
        out = sync_predictability_tiers_live(tiers if isinstance(tiers, list) else [])
        # 尚无 last 时勾选无法开闸：软失败（200），前端不弹错
        return {**out, "live": live_tier_status()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.put("/api/quant/research-universe")

def quant_research_universe_put(body: Dict[str, Any]) -> Dict[str, Any]:
    """写入日线研究宇宙 codes（去重，上限 2000）。不改观察池、不触发分钟暖仓。"""
    from core.research_universe import save_research_universe

    try:
        saved = save_research_universe(body if isinstance(body, dict) else {})
        from core.research_universe import build_research_universe_board

        board = build_research_universe_board(include_coverage=False)
        return {**saved, "board": board}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/research-universe/sync-watching")

def quant_research_universe_sync_watching() -> Dict[str, Any]:
    """用当前观察池覆盖研究宇宙（不触发分钟暖仓）。"""
    from core.research_universe import (
        build_research_universe_board,
        sync_research_universe_from_watching,
    )

    try:
        saved = sync_research_universe_from_watching()
        board = build_research_universe_board(include_coverage=False)
        return {**saved, "board": board}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/research-universe/sync-cached-daily")

def quant_research_universe_sync_cached_daily() -> Dict[str, Any]:
    """用本地日 K 仓（约 1200）覆盖研究宇宙；不改观察池、不暖仓。"""
    from core.research_universe import (
        build_research_universe_board,
        sync_research_universe_from_cached_daily,
    )

    try:
        saved = sync_research_universe_from_cached_daily()
        board = build_research_universe_board(include_coverage=False)
        return {**saved, "board": board}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/tc-ridge")

def quant_tc_ridge(body: TauRidgeRequest) -> Dict[str, Any]:
    """ŷ_τc Ridge。与 ``/api/quant/tau-ridge`` 同一套 live 模型（tau_ridge_model.json）。"""
    return quant_tau_ridge(body)

@router.get("/api/quant/tc-ridge/model")

def quant_tc_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_τc 模型（tau_ridge_model.json）。"""
    return quant_tau_ridge_model()

@router.post("/api/quant/t30-ridge")

def quant_t30_ridge(body: T30RidgeRequest) -> Dict[str, Any]:
    """ŷ_τ30：alpha_τh → P(窗收益>0) + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t30-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        tau_hm=body.tau_hm,
        tau_end_hm=body.tau_end_hm,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
        cross_section_zscore=bool(body.cross_section_zscore),
    )
    try:
        if body.persist or body.sync:
            return deps.quant.run_t30_ridge_experiment(**kwargs)
        return deps.quant.start_t30_ridge_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/t30-ridge/model")

def quant_t30_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_τ30 模型（若有）。"""
    try:
        return deps.quant.get_t30_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/t45-ridge")

def quant_t45_ridge(body: T45RidgeRequest) -> Dict[str, Any]:
    """ŷ_τ45：alpha_τh → P(窗收益>0) + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t45-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        tau_hm=body.tau_hm,
        tau_end_hm=body.tau_end_hm,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
        cross_section_zscore=bool(body.cross_section_zscore),
    )
    try:
        if body.persist or body.sync:
            return deps.quant.run_t45_ridge_experiment(**kwargs)
        return deps.quant.start_t45_ridge_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/t45-ridge/model")

def quant_t45_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_τ45 模型（若有）。"""
    try:
        return deps.quant.get_t45_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/t60-ridge")

def quant_t60_ridge(body: T60RidgeRequest) -> Dict[str, Any]:
    """ŷ_τ60：alpha_τh → P(窗收益>0) + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t60-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        tau_hm=body.tau_hm,
        tau_end_hm=body.tau_end_hm,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
        cross_section_zscore=bool(body.cross_section_zscore),
    )
    try:
        if body.persist or body.sync:
            return deps.quant.run_t60_ridge_experiment(**kwargs)
        return deps.quant.start_t60_ridge_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/t60-ridge/model")

def quant_t60_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_τ60 模型（若有）。"""
    try:
        return deps.quant.get_t60_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/t75-ridge")

def quant_t75_ridge(body: T75RidgeRequest) -> Dict[str, Any]:
    """ŷ_τ75：alpha_τh → P(窗收益>0) + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t75-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        tau_hm=body.tau_hm,
        tau_end_hm=body.tau_end_hm,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
        cross_section_zscore=bool(body.cross_section_zscore),
    )
    try:
        if body.persist or body.sync:
            return deps.quant.run_t75_ridge_experiment(**kwargs)
        return deps.quant.start_t75_ridge_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/t75-ridge/model")

def quant_t75_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_τ75 模型（若有）。"""
    try:
        return deps.quant.get_t75_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/t90-ridge")

def quant_t90_ridge(body: T90RidgeRequest) -> Dict[str, Any]:
    """ŷ_τ90：alpha_τh → P(窗收益>0) + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t90-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        tau_hm=body.tau_hm,
        tau_end_hm=body.tau_end_hm,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
        cross_section_zscore=bool(body.cross_section_zscore),
    )
    try:
        if body.persist or body.sync:
            return deps.quant.run_t90_ridge_experiment(**kwargs)
        return deps.quant.start_t90_ridge_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/t90-ridge/model")

def quant_t90_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_τ90 模型（若有）。"""
    try:
        return deps.quant.get_t90_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/factor-cs-ic")

def quant_factor_cs_ic(body: FactorCsIcRequest) -> Dict[str, Any]:
    """S1 · 研究池逐因子日频截面 IC（Pearson + Spearman）；不写 config。"""
    try:
        return deps.quant.run_factor_cs_ic_experiment(
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            watching_limit=body.watching_limit,
            min_names=body.min_names,
            pit_fundamentals=body.pit_fundamentals,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/weight-suggest")

def quant_weight_suggest(body: WeightSuggestRequest) -> Dict[str, Any]:
    try:
        return deps.quant.suggest_weights(
            body.code,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
            use_cs_ic=body.use_cs_ic,
            watching_limit=body.watching_limit,
            run_oos_gate=body.run_oos_gate,
            oos_tol_pp=body.oos_tol_pp,
            ridge_lambda=body.ridge_lambda,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/threshold-suggest")

def quant_threshold_suggest(body: ThresholdSuggestRequest) -> Dict[str, Any]:
    try:
        return deps.quant.suggest_thresholds(
            body.code,
            lookback=body.lookback,
            use_watching=body.use_watching,
            watching_limit=body.watching_limit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

_CORR_UNIVERSE = 40


def _watching_factor_items(limit: int = _CORR_UNIVERSE):
    """观察池前 N 只的日线截面因子分。

    只读 sub_scores，不走观察摘要（整池打分会超时，超时行没有 sub_scores）。
    """
    from core.data.service import get_research_service
    from core.signal.scorer import score_bars
    from core.watching.store import read_watching

    uni = read_watching()
    codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    cap = max(8, min(int(limit or _CORR_UNIVERSE), _CORR_UNIVERSE))
    codes = codes[:cap]
    if not codes:
        return [], "watching_bars", "观察池为空"
    packs = get_research_service().get_bars_batch(codes, limit=120)
    items: list = []
    for code, pack in zip(codes, packs):
        bars = pack.get("bars") if isinstance(pack, dict) else None
        if not isinstance(bars, list) or len(bars) < 20:
            continue
        scored = score_bars(bars, stock_code=code, mom3_hard_reject=False)
        subs = scored.get("sub_scores") if isinstance(scored, dict) else None
        if isinstance(subs, dict) and subs:
            items.append({"stock_code": code, "sub_scores": subs})
    note = "" if items else "观察池日线不足，没有截面因子分"
    return items, "watching_bars", note

@router.get("/api/quant/factor-ir")

def quant_factor_ir(
    lookback: int = 60,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """因子 IR 分析：因子信息比率 = IC 均值 / IC 标准差 × sqrt(252/horizon)。"""
    try:
        items, _source, empty_note = _watching_factor_items()
        if not items:
            return {"ok": True, "factors": [], "note": empty_note or "观察池还没有截面因子分"}

        factor_ic_series: Dict[str, List[float]] = {}
        for item in items:
            sub_scores = item.get("sub_scores") or {}
            for factor, score in sub_scores.items():
                if factor not in factor_ic_series:
                    factor_ic_series[factor] = []
                factor_ic_series[factor].append(float(score))

        factor_ir_list: List[Dict[str, Any]] = []
        for factor, scores in factor_ic_series.items():
            if len(scores) < 3:
                continue
            import statistics as _stats
            mean_ic = _stats.mean(scores)
            std_ic = _stats.stdev(scores) if len(scores) > 1 else 0
            ir = (mean_ic / std_ic) if std_ic > 1e-12 else 0
            ann_factor = (252.0 / max(horizon_days, 1)) ** 0.5
            ir_annual = ir * ann_factor
            factor_ir_list.append({
                "factor": factor,
                "mean_ic": round(mean_ic, 4),
                "std_ic": round(std_ic, 4),
                "ir": round(ir, 4),
                "ir_annual": round(ir_annual, 4),
                "sample_count": len(scores),
                "positive_rate": round(sum(1 for s in scores if s > 0) / len(scores) * 100, 1),
            })

        factor_ir_list.sort(key=lambda x: abs(x["ir_annual"]), reverse=True)

        return {
            "ok": True,
            "factors": factor_ir_list,
            "note": f"基于 {len(items)} 只观察池票的截面因子分数",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/expr-eval")

def quant_expr_eval(body: ExprEvalRequest) -> Dict[str, Any]:
    """DSL 表达式因子求值：本票时序值与 IC，以及观察池截面 Rank IC。"""
    try:
        return deps.quant.eval_factor_expr(
            code=body.code,
            expr=body.expr,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/research-task")

def quant_research_task(body: ResearchTaskRequest) -> Dict[str, Any]:
    """按注册表跑一个研究头，并写入实验记录。"""
    try:
        return deps.quant.run_research_task(body.head, **(body.params or {}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/experiments")

def quant_experiments(
    model_type: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """列出实验追踪器中的实验记录。"""
    try:
        return deps.quant.list_experiments(model_type=model_type, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
