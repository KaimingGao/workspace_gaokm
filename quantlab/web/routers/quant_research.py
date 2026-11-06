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


def _with_research_universe_board(
    payload: Dict[str, Any], *, include_subset_ok: bool = False
) -> Dict[str, Any]:
    """合并看板快照；Web 主入口可附 ``watching_subset_ok``。"""
    from core.research_universe import build_research_universe_board

    board = build_research_universe_board(include_coverage=False)
    out = {**payload, "board": board}
    if include_subset_ok:
        out["watching_subset_ok"] = board.get("watching_subset_ok")
    return out


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
            train_universe_tier_a=bool(body.train_universe_tier_a),
            train_tiers=list(body.train_tiers or []) or None,
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
            train_universe_tier_a=bool(body.train_universe_tier_a),
            train_tiers=list(body.train_tiers or []) or None,
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


@router.post("/api/quant/roll-gate-top1")
def quant_roll_gate_top1() -> Dict[str, Any]:
    """滚动验证：A 档 lookback500 · holdout10×3 · oo/τc 两臂；后台 Job，不写模型。

    轮询 ``GET /api/jobs/roll-gate-top1?progress=1``；折表读 ``GET /api/quant/roll-gate-top1/last``。
    """
    from core.research.roll_gate_top1 import start_roll_gate_top1_job

    try:
        return start_roll_gate_top1_job()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/quant/roll-gate-top1/progress")
def quant_roll_gate_top1_progress() -> Dict[str, Any]:
    """滚动验证进度（内存快照 + Job 槽）。"""
    from core.job_progress import roll_gate_top1_job
    from core.research.roll_gate_top1 import progress_snapshot

    try:
        roll_gate_top1_job.reclaim_if_stale()
    except Exception:  # noqa: BLE001
        pass
    job = roll_gate_top1_job.get()
    snap = progress_snapshot()
    # 进度轮询不附逐日明细，避免大 JSON
    slim = {
        "status": snap.get("status"),
        "message": snap.get("message"),
        "fold": snap.get("fold"),
        "folds": snap.get("folds"),
        "job_id": snap.get("job_id"),
    }
    partial = snap.get("partial")
    if isinstance(partial, dict):
        slim["partial"] = {
            "success": partial.get("success"),
            "partial": partial.get("partial"),
            "job_id": partial.get("job_id") or snap.get("job_id"),
            "params": partial.get("params"),
            "window": partial.get("window"),
            "summary": partial.get("summary"),
            "finished_at": partial.get("finished_at"),
            "updated_at": partial.get("updated_at"),
            "folds": [
                {
                    "fold": f.get("fold"),
                    "eval_start": f.get("eval_start"),
                    "eval_end": f.get("eval_end"),
                    "fit_end": f.get("fit_end"),
                    "ok": f.get("ok"),
                    "error": f.get("error"),
                    "elapsed_s": f.get("elapsed_s"),
                    "oo": {
                        k: v
                        for k, v in (f.get("oo") or {}).items()
                        if k != "days"
                    }
                    if isinstance(f.get("oo"), dict)
                    else f.get("oo"),
                    "tc": {
                        k: v
                        for k, v in (f.get("tc") or {}).items()
                        if k != "days"
                    }
                    if isinstance(f.get("tc"), dict)
                    else f.get("tc"),
                }
                for f in (partial.get("folds") or [])
                if isinstance(f, dict)
            ],
        }
    return {"ok": True, "progress": slim, "job": job}


@router.get("/api/quant/roll-gate-top1/last")
def quant_roll_gate_top1_last() -> Dict[str, Any]:
    """读取上次滚动验证结果（含逐日明细）。"""
    from core.research.roll_gate_top1 import attach_saved_at, last_path, load_last

    try:
        path = last_path()
        last = load_last()
        if not last:
            return {
                "success": False,
                "exists": False,
                "path": path,
                "note": "尚无滚动验证结果；请点「滚动验证」",
            }
        out = attach_saved_at(dict(last), path)
        out["exists"] = True
        out["path"] = path
        return out
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
    holdout: int = Query(
        60,
        ge=2,
        le=90,
        description="分档专用 Holdout 交易日数（评测窗；与 ŷ_oo 拟合 Holdout 独立；默认 60）",
    ),
    head: str = "oo",
    pool: str = "watching",
    lookback: int = 120,
    a_count: int = Query(
        100, ge=1, le=2000, description="A 档：Lift_Score 序 top 数量（默认 100）"
    ),
    b_count: int = Query(
        300, ge=0, le=5000, description="B 档：接着取的数量（默认 300；其余为 C）"
    ),
) -> Dict[str, Any]:
    """观察池分档：Holdout 前 60 日猜涨 lift（说涨精度 − 上涨日比例），落盘 last。

    表内四头 lift + Lift_Score；档标题 Holdout Lift_Score 均值与等权买持看 Holdout。
    holdout: 分档专用评测窗（默认 60），与 ŷ_oo 拟合卡 Holdout 独立；不参与定档训练。
    lookback: 面板长度。定档模型只拟合定档窗之前，不覆盖研究套。
    有分数者 top a_count→A、接着 b_count→B、其余→C。
    回测天数由回测页 lookback 独立设置。
    """
    from core.research.predictability_tiers import (
        build_predictability_tiers,
        clamp_a_count,
        clamp_b_count,
        live_tier_status,
    )

    try:
        rep = build_predictability_tiers(
            holdout_n=max(2, min(int(holdout or 60), 90)),
            pool=str(pool or "watching"),
            head=str(head or "oo"),
            lookback=max(100, min(int(lookback or 120), 1000)),
            a_count=clamp_a_count(a_count),
            b_count=clamp_b_count(b_count),
            persist=True,
        )
        if isinstance(rep, dict):
            rep["live"] = live_tier_status()
        return rep
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.get("/api/quant/research-universe/predictability-tiers/progress")

def quant_research_universe_predictability_tiers_progress() -> Dict[str, Any]:
    """分档进行到哪一步。长请求期间供状态条轮询。"""
    from core.research.predictability_tiers import tier_progress_snapshot

    return tier_progress_snapshot()


@router.get("/api/quant/research-universe/predictability-tiers/last")

def quant_research_universe_predictability_tiers_last(
    slim: bool = Query(False, description="去掉 ŷ/实现序列，只留档位摘要"),
) -> Dict[str, Any]:
    """读取上次可预测性分档影子报告 + live 闸状态（档位随历史回测勾选）。"""
    from core.research.predictability_tiers import (
        live_tier_status,
        attach_saved_at,
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
        out = attach_saved_at(out, out["path"])
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
    """写入研究宇宙 codes（默认可交易严筛，上限 2000）。不改观察池、不暖仓。"""
    from core.research_universe import save_research_universe

    try:
        saved = save_research_universe(body if isinstance(body, dict) else {})
        return _with_research_universe_board(saved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

@router.post("/api/quant/research-universe/rebuild-tradable")

def quant_research_universe_rebuild_tradable() -> Dict[str, Any]:
    """可交易严筛重建研究宇宙（日 K ∪ 现货）；不改观察池、不暖仓。运维分步用。"""
    from core.universe.tradable_screen import rebuild_tradable_research_universe

    try:
        saved = rebuild_tradable_research_universe()
        return _with_research_universe_board(saved)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/research-universe/reconcile-watching")

def quant_research_universe_reconcile_watching() -> Dict[str, Any]:
    """观察池裁成研究宇宙子集（``QUANTLAB_WATCHING_SUBSET_OF_RESEARCH``）。运维分步用。"""
    from core.watching.repository import reconcile_watching_to_research

    try:
        out = reconcile_watching_to_research()
        return _with_research_universe_board(out)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/quant/research-universe/rebuild-and-reconcile")

def quant_research_universe_rebuild_and_reconcile() -> Dict[str, Any]:
    """重建研究宇宙并立刻对齐观察池（Web 单按钮主入口）。"""
    from core.universe.tradable_screen import rebuild_tradable_research_universe
    from core.watching.repository import reconcile_watching_to_research

    try:
        saved = rebuild_tradable_research_universe()
        rec = reconcile_watching_to_research()
        return _with_research_universe_board(
            {**saved, "reconcile": rec}, include_subset_ok=True
        )
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
