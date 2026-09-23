"""量化研究台 API — factor research。"""


import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

from web import deps
from web.schemas import (
    CrossSectionRequest,
    ExcessModeShadowRequest,
    FactorCsIcRequest,
    FactorExperimentRequest,
    FactorOlsPoolRequest,
    CoRidgeRequest,
    TcRidgeRequest,
    T30RidgeRequest,
    T45RidgeRequest,
    T60RidgeRequest,
    T75RidgeRequest,
    T90RidgeRequest,
    T30TreeRequest,
    T45TreeRequest,
    T60TreeRequest,
    T75TreeRequest,
    T90TreeRequest,
    TcTreeRequest,
    RemRidgeRequest,
    TauRidgeRequest,
    TauTreeRequest,
    TauBoostRequest,
    ThresholdSuggestRequest,
    WeightSuggestRequest,
    YhatResidualShadowRequest,
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


@router.post("/api/quant/tau-tree")
def quant_tau_tree(body: TauTreeRequest) -> Dict[str, Any]:
    """ŷ_τ_tree 影子头 + 同 Holdout Ridge；不写 live / 研究套，不进回测。"""
    try:
        return deps.quant.run_tau_tree_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            tau_hm=body.tau_hm,
            holdout_trading_days=body.holdout_trading_days,
            backend=body.backend,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/tau-tree/last")
def quant_tau_tree_last() -> Dict[str, Any]:
    """读取上次 ŷ_τ_tree（tau_tree_last_report.json）；不进打分。"""
    try:
        return deps.quant.get_tau_tree_last_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/tau-boost")
def quant_tau_boost(body: TauBoostRequest) -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/tau-tree``。"""
    return quant_tau_tree(body)


@router.get("/api/quant/tau-boost/last")
def quant_tau_boost_last() -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/tau-tree/last``。"""
    return quant_tau_tree_last()


@router.post("/api/quant/tau-ridge")
def quant_tau_ridge(body: TauRidgeRequest) -> Dict[str, Any]:
    """ŷ_τ Ridge + 时间 OOS；可选 persist 到 live/tau_ridge_model.json。"""
    try:
        return deps.quant.run_tau_ridge_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            persist=body.persist,
            note=body.note,
            tau_hm=body.tau_hm,
            force_promote=body.force_promote,
            persist_role=body.persist_role,
            holdout_trading_days=body.holdout_trading_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/rem-ridge")
def quant_rem_ridge(body: RemRidgeRequest) -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/tau-ridge``。"""
    return quant_tau_ridge(body)


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


@router.post("/api/quant/excess-mode/shadow")
def quant_excess_mode_shadow(body: ExcessModeShadowRequest) -> Dict[str, Any]:
    """绝对 y vs 指数超额 y：同池 holdout IC 影子对照（不写盘）。"""
    try:
        return deps.quant.run_excess_mode_shadow(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            horizon_days=body.horizon_days,
            ridge_lambda=body.ridge_lambda,
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


@router.get("/api/quant/rem-ridge/model")
def quant_rem_ridge_model() -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/tau-ridge/model``。"""
    return quant_tau_ridge_model()


@router.post("/api/quant/co-ridge")
def quant_co_ridge(body: CoRidgeRequest) -> Dict[str, Any]:
    """隔夜缺口 Ridge：open[T+1]/close[T]-1 + 时间 OOS；可选 persist 到 live。"""
    try:
        return deps.quant.run_co_ridge_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            persist=body.persist,
            note=body.note,
            persist_role=body.persist_role,
            holdout_trading_days=body.holdout_trading_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/co-ridge/model")
def quant_co_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_co 模型（主路径 co_ridge_model.json；可读旧 on 文件）。"""
    try:
        return deps.quant.get_co_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/on-ridge")
def quant_on_ridge(body: CoRidgeRequest) -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/co-ridge``。"""
    return quant_co_ridge(body)


@router.get("/api/quant/on-ridge/model")
def quant_on_ridge_model() -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/co-ridge/model``。"""
    return quant_co_ridge_model()


@router.post("/api/quant/path-ridge")
def quant_path_ridge() -> Dict[str, Any]:
    """ŷ_hl / path_ridge 产品面已退役（研究模块仍保留）。"""
    raise HTTPException(
        status_code=410,
        detail="ŷ_hl / path_ridge is retired",
    )


@router.get("/api/quant/path-ridge/model")
def quant_path_ridge_model() -> Dict[str, Any]:
    """ŷ_hl / path_ridge 产品面已退役（研究模块仍保留）。"""
    raise HTTPException(
        status_code=410,
        detail="ŷ_hl / path_ridge is retired",
    )


@router.post("/api/quant/tc-ridge")
def quant_tc_ridge(body: TcRidgeRequest) -> Dict[str, Any]:
    """ŷ_τc Ridge：与 ŷ_oc 同 X → close[T]/price(τ)−1 + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/tc-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        minute_lookback_days=body.minute_lookback_days,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
    )
    try:
        if body.persist or body.sync:
            return deps.quant.run_tc_ridge_experiment(**kwargs)
        return deps.quant.start_tc_ridge_job(**kwargs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/tc-ridge/model")
def quant_tc_ridge_model() -> Dict[str, Any]:
    """读取已 promote 的 ŷ_τc 模型（若有）。"""
    try:
        return deps.quant.get_tc_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/r-ridge")
def quant_r_ridge(body: TcRidgeRequest) -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/tc-ridge``。"""
    return quant_tc_ridge(body)


@router.get("/api/quant/r-ridge/model")
def quant_r_ridge_model() -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/tc-ridge/model``。"""
    return quant_tc_ridge_model()


@router.post("/api/quant/t30-ridge")
def quant_t30_ridge(body: T30RidgeRequest) -> Dict[str, Any]:
    """ŷ_τ30 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕25/30/35))/price(τ)−1 + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t30-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        minute_lookback_days=body.minute_lookback_days,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
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
    """ŷ_τ45 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕40/45/50))/price(τ)−1 + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t45-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        minute_lookback_days=body.minute_lookback_days,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
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
    """ŷ_τ60 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕55/60/65))/price(τ)−1 + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t60-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        minute_lookback_days=body.minute_lookback_days,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
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
    """ŷ_τ75 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕70/75/80))/price(τ)−1 + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t75-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        minute_lookback_days=body.minute_lookback_days,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
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
    """ŷ_τ90 Ridge：与 ŷ_oc 同 X → mean(price(τ⊕85/90/95))/price(τ)−1 + 时间 OOS；可选 persist。

    ``persist=true`` / ``sync=true`` 同步；否则入队 ``GET /api/jobs/t90-ridge``。
    """
    kwargs = dict(
        lookback=body.lookback,
        watching_limit=body.watching_limit,
        ridge_lambda=body.ridge_lambda,
        gap_trigger_pct=body.gap_trigger_pct,
        minute_period=body.minute_period,
        minute_lookback_days=body.minute_lookback_days,
        persist=body.persist,
        force_promote=body.force_promote,
        note=body.note,
        persist_role=body.persist_role,
        holdout_trading_days=body.holdout_trading_days,
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


@router.post("/api/quant/tc-tree")
def quant_tc_tree(body: TcTreeRequest) -> Dict[str, Any]:
    """ŷ_τc_tree 影子头 + 同 Holdout Ridge；不写 live / 研究套，不进回测。"""
    try:
        return deps.quant.run_tc_tree_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            tau_hm=body.tau_hm,
            holdout_trading_days=body.holdout_trading_days,
            backend=body.backend,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/tc-tree/last")
def quant_tc_tree_last() -> Dict[str, Any]:
    """读取上次 ŷ_τc_tree（tc_tree_last_report.json）；不进打分。"""
    try:
        return deps.quant.get_tc_tree_last_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/r-tree")
def quant_r_tree(body: TcTreeRequest) -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/tc-tree``。"""
    return quant_tc_tree(body)


@router.get("/api/quant/r-tree/last")
def quant_r_tree_last() -> Dict[str, Any]:
    """兼容旧路径 → 同 ``/api/quant/tc-tree/last``。"""
    return quant_tc_tree_last()


@router.post("/api/quant/t30-tree")
def quant_t30_tree(body: T30TreeRequest) -> Dict[str, Any]:
    """ŷ_τ30_tree 影子头 + 同 Holdout Ridge；不写 live / 研究套，不进回测。"""
    try:
        return deps.quant.run_t30_tree_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            tau_hm=body.tau_hm,
            holdout_trading_days=body.holdout_trading_days,
            backend=body.backend,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/t30-tree/last")
def quant_t30_tree_last() -> Dict[str, Any]:
    """读取上次 ŷ_τ30_tree（t30_tree_last_report.json）；不进打分。"""
    try:
        return deps.quant.get_t30_tree_last_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/t45-tree")
def quant_t45_tree(body: T45TreeRequest) -> Dict[str, Any]:
    """ŷ_τ45_tree 影子头 + 同 Holdout Ridge；不写 live / 研究套，不进回测。"""
    try:
        return deps.quant.run_t45_tree_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            tau_hm=body.tau_hm,
            holdout_trading_days=body.holdout_trading_days,
            backend=body.backend,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/t45-tree/last")
def quant_t45_tree_last() -> Dict[str, Any]:
    """读取上次 ŷ_τ45_tree（t45_tree_last_report.json）；不进打分。"""
    try:
        return deps.quant.get_t45_tree_last_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/t60-tree")
def quant_t60_tree(body: T60TreeRequest) -> Dict[str, Any]:
    """ŷ_τ60_tree 影子头 + 同 Holdout Ridge；不写 live / 研究套，不进回测。"""
    try:
        return deps.quant.run_t60_tree_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            tau_hm=body.tau_hm,
            holdout_trading_days=body.holdout_trading_days,
            backend=body.backend,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/t60-tree/last")
def quant_t60_tree_last() -> Dict[str, Any]:
    """读取上次 ŷ_τ60_tree（t60_tree_last_report.json）；不进打分。"""
    try:
        return deps.quant.get_t60_tree_last_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/t75-tree")
def quant_t75_tree(body: T75TreeRequest) -> Dict[str, Any]:
    """ŷ_τ75_tree 影子头 + 同 Holdout Ridge；不写 live / 研究套，不进回测。"""
    try:
        return deps.quant.run_t75_tree_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            tau_hm=body.tau_hm,
            holdout_trading_days=body.holdout_trading_days,
            backend=body.backend,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/t75-tree/last")
def quant_t75_tree_last() -> Dict[str, Any]:
    """读取上次 ŷ_τ75_tree（t75_tree_last_report.json）；不进打分。"""
    try:
        return deps.quant.get_t75_tree_last_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/t90-tree")
def quant_t90_tree(body: T90TreeRequest) -> Dict[str, Any]:
    """ŷ_τ90_tree 影子头 + 同 Holdout Ridge；不写 live / 研究套，不进回测。"""
    try:
        return deps.quant.run_t90_tree_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            tau_hm=body.tau_hm,
            holdout_trading_days=body.holdout_trading_days,
            backend=body.backend,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/t90-tree/last")
def quant_t90_tree_last() -> Dict[str, Any]:
    """读取上次 ŷ_τ90_tree（t90_tree_last_report.json）；不进打分。"""
    try:
        return deps.quant.get_t90_tree_last_report()
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


@router.get("/api/quant/factor-corr")
def quant_factor_corr(
    min_samples: int = 3,
    threshold: float = 0.7,
) -> Dict[str, Any]:
    """因子相关性矩阵：基于簿 / 观察池 sub_scores 算截面 Pearson。"""
    try:
        from core.signal.factors.meta.corr import compute_factor_corr_matrix, redundancy_warnings_from_corr

        items: list = []
        source = "empty"
        # 优先 active 簿 scored_all（含 sub_scores，且不重打全池）
        try:
            from core.signal.cluster.live import load_active_cluster_book

            doc = load_active_cluster_book() or {}
            by_code: dict = {}
            for row in list(doc.get("scored_all") or []) + list(doc.get("book") or []):
                if not isinstance(row, dict):
                    continue
                code = str(row.get("stock_code") or "").strip()
                if not code or not (row.get("sub_scores") or {}):
                    continue
                by_code[code] = row
            if len(by_code) >= int(min_samples):
                items = list(by_code.values())
                source = "cluster_book"
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in quant_research.py", exc_info=True)
            pass

        if not items:
            try:
                from core.watching.insights import load_insights_cache

                insights = load_insights_cache()
                if insights:
                    raw = (
                        insights
                        if isinstance(insights, list)
                        else list(insights.values())
                        if isinstance(insights, dict)
                        else []
                    )
                    items = [it for it in raw if isinstance(it, dict) and (it.get("sub_scores") or {})]
                    source = "watching_insights"
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in quant_research.py", exc_info=True)
                pass

        if not items:
            return {
                "ok": True,
                "success": False,
                "factors": [],
                "matrix": {},
                "pairs": [],
                "warnings": [],
                "source": source,
                "note": "无 sub_scores：请先「跑分组 / 刷新簿」生成因子分数",
            }

        result = compute_factor_corr_matrix(items, min_samples=min_samples)
        warnings = redundancy_warnings_from_corr(result, threshold=threshold)
        result["warnings"] = warnings
        result["source"] = source
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/factor-ir")
def quant_factor_ir(
    lookback: int = 60,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """因子 IR 分析：因子信息比率 = IC 均值 / IC 标准差 × sqrt(252/horizon)。"""
    try:
        from core.watching.insights import load_insights_cache

        insights = load_insights_cache()
        if not insights:
            return {"ok": True, "factors": [], "note": "无观察池数据"}

        items = insights if isinstance(insights, list) else list(insights.values()) if isinstance(insights, dict) else []

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
