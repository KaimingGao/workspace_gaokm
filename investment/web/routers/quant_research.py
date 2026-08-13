"""量化研究台 API — factor research。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    CrossSectionRequest,
    FactorCsIcRequest,
    FactorExperimentRequest,
    FactorOlsPoolRequest,
    RemRidgeRequest,
    ThresholdSuggestRequest,
    WeightSuggestRequest,
)

router = APIRouter(tags=["quant"])


@router.get("/api/quant/factors")
def quant_factors():
    return deps.quant.list_factors()


@router.get("/api/quant/factor-panel")
def quant_factor_panel(
    code: str = "茅台",
    with_experiment: bool = False,
    lookback: int = 120,
    horizon_days: int = 3,
):
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
def quant_cross_section(body: CrossSectionRequest):
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
def quant_factor_experiment(body: FactorExperimentRequest):
    try:
        return deps.quant.run_factor_experiment(
            body.code,
            lookback=body.lookback,
            horizon_days=body.horizon_days,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/factor-ols")
def quant_factor_ols(body: FactorExperimentRequest):
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
def quant_factor_ols_pool(body: FactorOlsPoolRequest):
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


@router.post("/api/quant/rem-ridge")
def quant_rem_ridge(body: RemRidgeRequest):
    """R0：open→close 剩余收益头 Ridge + 时间 OOS；可选 persist 到 live。"""
    try:
        return deps.quant.run_rem_ridge_experiment(
            lookback=body.lookback,
            watching_limit=body.watching_limit,
            ridge_lambda=body.ridge_lambda,
            gap_trigger_pct=body.gap_trigger_pct,
            theme_boost=body.theme_boost,
            persist=body.persist,
            note=body.note,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/rem-ridge/model")
def quant_rem_ridge_model():
    """读取已 promote 的 rem 模型（若有）。"""
    try:
        return deps.quant.get_rem_ridge_model()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/factor-cs-ic")
def quant_factor_cs_ic(body: FactorCsIcRequest):
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
def quant_weight_suggest(body: WeightSuggestRequest):
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
def quant_threshold_suggest(body: ThresholdSuggestRequest):
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
):
    """因子相关性矩阵：基于观察池 sub_scores 算截面 Pearson。"""
    try:
        from core.signal.factor_corr import compute_factor_corr_matrix, redundancy_warnings_from_corr
        from core.watching_insights import load_insights_cache

        items = []
        try:
            insights = load_insights_cache()
            if insights:
                items = insights if isinstance(insights, list) else list(insights.values()) if isinstance(insights, dict) else []
        except Exception:
            pass

        if not items:
            return {
                "ok": True,
                "success": False,
                "factors": [],
                "matrix": {},
                "pairs": [],
                "warnings": [],
                "note": "无观察池数据，请先运行「跑分组」生成因子分数",
            }

        result = compute_factor_corr_matrix(items, min_samples=min_samples)
        warnings = redundancy_warnings_from_corr(result, threshold=threshold)
        result["warnings"] = warnings
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/factor-ir")
def quant_factor_ir(
    lookback: int = 60,
    horizon_days: int = 3,
):
    """因子 IR 分析：因子信息比率 = IC 均值 / IC 标准差 × sqrt(252/horizon)。"""
    try:
        from core.watching_insights import load_insights_cache

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
