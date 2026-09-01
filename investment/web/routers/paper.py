"""纸面账户 API（模拟交易）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    PaperBuyRequest,
    PaperCostModelRequest,
    PaperDepositRequest,
    PaperExecutionPatchRequest,
    PaperRebalanceRequest,
    PaperRunRequest,
    PaperSellRequest,
    PaperT0Request,
    PaperT0AutoRequest,
    PaperT0WorkerRequest,
    PaperT0DeleteRequest,
    PaperT0IntradayClearRequest,
)

router = APIRouter(tags=["paper"])


@router.get("/api/paper")
def paper_status(lite: bool = False) -> Dict[str, Any]:
    """账户摘要。lite=1 时跳过盯市/打分，供数据中心快速取已持码。"""
    return deps.paper.status(lite=bool(lite))


@router.get("/api/paper/holding-scores")
def paper_holding_scores(offline_only: bool = True) -> Dict[str, Any]:
    """持仓 ŷ：默认仅本地仓。UI 恒传 offline_only=true；可拉远端先走增量补齐。"""
    try:
        from core.signal.score_display import json_safe

        return json_safe(deps.paper.holding_scores(offline_only=bool(offline_only)))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/paper/execution")
def paper_execution(channel: str = "paper") -> Dict[str, Any]:
    """生效 ExecutionSpec（含做 T overlay）；channel=paper|backtest。"""
    ch = (channel or "paper").strip().lower()
    if ch not in {"paper", "backtest"}:
        raise HTTPException(status_code=400, detail="channel 须为 paper 或 backtest")
    try:
        return deps.paper.execution_status(channel=ch)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/paper/execution/diff")
def paper_execution_diff() -> Dict[str, Any]:
    """纸面覆盖 vs 策略 Spec 默认。"""
    try:
        return deps.paper.execution_diff()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/execution")
def paper_execution_save(body: PaperExecutionPatchRequest | None = None) -> Dict[str, Any]:
    """保存账户级做T / coupling 覆盖（不改 StrategySpec 源）。"""
    try:
        req = body or PaperExecutionPatchRequest()
        t0 = dict(req.t0 or {})
        for k, v in (
            ("enabled", req.enabled),
            ("t0_ratio", req.t0_ratio),
            ("fill_mode", req.fill_mode),
            ("fill_mode_sell_then_buy", req.fill_mode_sell_then_buy),
            ("fill_mode_buy_then_sell", req.fill_mode_buy_then_sell),
            ("direction", req.direction),
            ("path_mode", req.path_mode),
            ("dir_enter", req.dir_enter),
            ("min_range_pct", req.min_range_pct),
            ("min_range_pct_sell_then_buy", req.min_range_pct_sell_then_buy),
            ("min_range_pct_buy_then_sell", req.min_range_pct_buy_then_sell),
            ("use_atr", req.use_atr),
            ("must_cover_same_day", req.must_cover_same_day),
            ("must_cover_same_day_sell_then_buy", req.must_cover_same_day_sell_then_buy),
            ("must_cover_same_day_buy_then_sell", req.must_cover_same_day_buy_then_sell),
            ("y_trade_enter", req.y_trade_enter),
            ("y_trade_strong", req.y_trade_strong),
            ("y_trade_floor", req.y_trade_floor),
            ("y_tau_enter", req.y_tau_enter),
            ("y_tau_enter_strong", req.y_tau_enter_strong),
            ("y_tau_enter_sell_then_buy", req.y_tau_enter_sell_then_buy),
            ("y_tau_enter_buy_then_sell", req.y_tau_enter_buy_then_sell),
            ("y_ratio_cut", req.y_ratio_cut),
            ("y_ratio_boost_cap", req.y_ratio_boost_cap),
            ("y_eod_prior", req.y_eod_prior),
            ("y_eod_enter", req.y_eod_enter),
            ("y_eod_strong", req.y_eod_strong),
            ("y_eod_tau_sign_gate", req.y_eod_tau_sign_gate),
            ("y_trade_tau_sign_gate", req.y_trade_tau_sign_gate),
            ("y_on_allow", req.y_on_allow),
            ("y_on_risk", req.y_on_risk),
            ("y_block_tau_nowcast_sign", req.y_block_tau_nowcast_sign),
            ("y_nc_enter", req.y_nc_enter),
            ("y_nc_strong", req.y_nc_strong),
            ("y_nowcast_enter", req.y_nowcast_enter),
            ("y_tau_map", req.y_tau_map),
            ("y_use_path", req.y_use_path),
            ("y_path_enter", req.y_path_enter),
            ("y_path_enter_sell_then_buy", req.y_path_enter_sell_then_buy),
            ("y_path_enter_buy_then_sell", req.y_path_enter_buy_then_sell),
            ("y_path_required", req.y_path_required),
            ("y_gap_tier_mode", req.y_gap_tier_mode),
            ("y_gap_tier_pct", req.y_gap_tier_pct),
            ("y_nowcast_oc_gate", req.y_nowcast_oc_gate),
            ("y_path_abandon_enabled", req.y_path_abandon_enabled),
            ("y_path_abandon_bars", req.y_path_abandon_bars),
            ("y_prefix_segment_enabled", req.y_prefix_segment_enabled),
            ("y_prefix_segment_enabled_sell_then_buy", req.y_prefix_segment_enabled_sell_then_buy),
            ("y_prefix_segment_enabled_buy_then_sell", req.y_prefix_segment_enabled_buy_then_sell),
            ("y_prefix_upbar_ratio_buy_then_sell", req.y_prefix_upbar_ratio_buy_then_sell),
            ("y_prefix_downbar_ratio_sell_then_buy", req.y_prefix_downbar_ratio_sell_then_buy),
            ("y_tau_entry_price_skip", req.y_tau_entry_price_skip),
            ("y_tau_entry_price_mult", req.y_tau_entry_price_mult),
            ("y_tau_entry_price_skip_buy_then_sell", req.y_tau_entry_price_skip_buy_then_sell),
            ("y_tau_entry_price_mult_buy_then_sell", req.y_tau_entry_price_mult_buy_then_sell),
            ("y_tau_entry_price_skip_sell_then_buy", req.y_tau_entry_price_skip_sell_then_buy),
            ("y_tau_entry_price_mult_sell_then_buy", req.y_tau_entry_price_mult_sell_then_buy),
            ("y_tau_exit_price_skip", req.y_tau_exit_price_skip),
            ("y_tau_exit_price_mult", req.y_tau_exit_price_mult),
            ("y_tau_exit_price_skip_buy_then_sell", req.y_tau_exit_price_skip_buy_then_sell),
            ("y_tau_exit_price_mult_buy_then_sell", req.y_tau_exit_price_mult_buy_then_sell),
            ("y_tau_exit_price_skip_sell_then_buy", req.y_tau_exit_price_skip_sell_then_buy),
            ("y_tau_exit_price_mult_sell_then_buy", req.y_tau_exit_price_mult_sell_then_buy),
            ("y_score_source", req.y_score_source),
            ("t0_pm_degrade", req.t0_pm_degrade),
            ("t0_pm_degrade_sell_then_buy", req.t0_pm_degrade_sell_then_buy),
            ("t0_pm_degrade_buy_then_sell", req.t0_pm_degrade_buy_then_sell),
            ("t0_pm_chase_interval_min", req.t0_pm_chase_interval_min),
            ("t0_pm_chase_interval_min_sell_then_buy", req.t0_pm_chase_interval_min_sell_then_buy),
            ("t0_pm_chase_interval_min_buy_then_sell", req.t0_pm_chase_interval_min_buy_then_sell),
            ("t0_stop_pct_buy_then_sell", req.t0_stop_pct_buy_then_sell),
            ("t0_stop_pct_sell_then_buy", req.t0_stop_pct_sell_then_buy),
            ("t0_stop_arm_bars", req.t0_stop_arm_bars),
            ("t0_stop_on_close", req.t0_stop_on_close),
        ):
            if v is not None and k not in t0:
                t0[k] = v
        payload = {
            "t0": t0,
            "coupling": req.coupling,
            "lock": req.lock,
        }
        if isinstance(req.rebalance_timing, dict):
            payload["rebalance_timing"] = req.rebalance_timing
        out = deps.paper.save_execution(payload, note=req.note or "")
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=out.get("errors") or out)
        return out
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/paper/execution/reset")
def paper_execution_reset() -> Dict[str, Any]:
    """清除账户级覆盖，恢复策略默认。"""
    try:
        return deps.paper.reset_execution()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/paper/holding-chart")
def paper_holding_chart(code: str, lookback: int = 60) -> Dict[str, Any]:
    """单只持仓日线收盘 / 相对成本浮盈曲线。"""
    try:
        return deps.paper.holding_chart(code, lookback=lookback)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/init")
def paper_init() -> Dict[str, Any]:
    try:
        return deps.paper.init()
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/api/paper/deposit")
def paper_deposit(body: PaperDepositRequest) -> Dict[str, Any]:
    """假账注资（增加现金）。"""
    try:
        return deps.paper.deposit(body.amount)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/withdraw")
def paper_withdraw(body: PaperDepositRequest) -> Dict[str, Any]:
    """假账减资（减少现金）。"""
    try:
        return deps.paper.withdraw(body.amount)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/reset")
def paper_reset() -> Dict[str, Any]:
    """测试基线回零：保留持仓，盈亏与曲线从当前净值重新起算。"""
    try:
        return deps.paper.reset()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/cost-model")
def paper_cost_model(body: PaperCostModelRequest) -> Dict[str, Any]:
    """切换模拟成交成本模型（zero | simple_cn）。"""
    try:
        return deps.paper.set_cost_model(body.cost_model)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/run")
def paper_run(body: PaperRunRequest) -> Dict[str, Any]:
    try:
        if body.background:
            return deps.paper.start_run_job(
                simulate_buy=body.simulate_buy,
                strategy=body.strategy,
                dry_run=body.dry_run,
            )
        return deps.paper.run(
            simulate_buy=body.simulate_buy,
            strategy=body.strategy,
            dry_run=body.dry_run,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/paper/job")
def paper_job() -> Dict[str, Any]:
    out = deps.paper.get_job()
    return {**out, "deprecated": True, "canonical": "/api/jobs/paper"}


@router.post("/api/paper/rebalance")
def paper_rebalance(body: PaperRebalanceRequest) -> Dict[str, Any]:
    try:
        from core.signal.score_display import json_safe

        return json_safe(
            deps.paper.rebalance(
                top_k=body.top_k,
                limit=body.limit,
                cluster_mode=bool(body.cluster_mode),
                matrix_mode=bool(body.matrix_mode),
                dry_run=bool(body.dry_run),
                strategy=body.strategy,
                offline_only=bool(body.offline_only),
            )
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(
            status_code=503,
            detail=f"{e}（账本忙或上次调仓未释放锁，请稍后重试；必要时重启 web）",
        ) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/buy")
def paper_buy(body: PaperBuyRequest) -> Dict[str, Any]:
    """手动加仓（金额或股数，现价假买）。"""
    if body.amount is None and body.shares is None:
        raise HTTPException(status_code=400, detail="请填写金额或股数")
    try:
        return deps.paper.buy(
            stock_code=body.stock_code,
            amount=body.amount,
            shares=body.shares,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/sell")
def paper_sell(body: PaperSellRequest) -> Dict[str, Any]:
    """手动减仓 / 清仓。勾选多只时整仓卖出。"""
    try:
        return deps.paper.sell(
            codes=body.codes,
            stock_code=body.stock_code,
            shares=body.shares,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/t0")
def paper_t0(body: PaperT0Request | None = None) -> Dict[str, Any]:
    """纸面底仓做 T（非实盘）。默认 dry_run 预演；confirm=true 才写账。
    仅 5m 第一触达；缺分钟线的票跳过（已删除日线模拟）。"""
    try:
        req = body or PaperT0Request()
        dry_run = bool(req.dry_run) and not bool(req.confirm)
        return deps.paper.simulate_t0(dry_run=dry_run, log_source="paper_t0")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/paper/t0/auto")
def paper_t0_auto_get() -> Dict[str, Any]:
    """自动做 T 配置与上次运行摘要。"""
    try:
        return deps.paper.t0_auto_status()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/t0/auto")
def paper_t0_auto_post(body: PaperT0AutoRequest | None = None) -> Dict[str, Any]:
    """保存自动做 T 开关/调度，或立即运行（run_now）。"""
    try:
        req = body or PaperT0AutoRequest()
        patch: Dict[str, Any] = {}
        if req.enabled is not None:
            patch["enabled"] = bool(req.enabled)
        if req.schedule:
            patch["schedule"] = str(req.schedule).strip()
        if patch:
            deps.paper.save_t0_auto(patch)
        if req.run_now:
            return deps.paper.run_t0_auto(
                dry_run=bool(req.dry_run),
                force=bool(req.force),
            )
        return deps.paper.t0_auto_status()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/paper/t0/worker")
def paper_t0_worker_get() -> Dict[str, Any]:
    """自动做 T 后台 worker 状态（是否运行中）。"""
    try:
        return deps.paper.t0_worker_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/t0/worker")
def paper_t0_worker_post(body: PaperT0WorkerRequest) -> Dict[str, Any]:
    """启动/停止 Web 内后台 worker。"""
    try:
        return deps.paper.set_t0_worker(bool(body.enabled))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/t0/last-run/delete")
def paper_t0_last_run_delete(body: PaperT0DeleteRequest) -> Dict[str, Any]:
    """删除落账明细；默认冲正对应做 T 成交腿。"""
    try:
        return deps.paper.delete_t0_records(
            stock_codes=list(body.stock_codes or []),
            reverse_ledger=bool(body.reverse_ledger),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/t0/intraday/clear")
def paper_t0_intraday_clear(body: PaperT0IntradayClearRequest) -> Dict[str, Any]:
    """清理今日盯盘状态（不冲正账本）。"""
    try:
        return deps.paper.clear_t0_intraday(
            stock_codes=list(body.stock_codes or []) or None,
            clear_all=bool(body.clear_all),
            force=bool(body.force),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/api/paper/clear-records")
def paper_clear_records(body: dict) -> Dict[str, Any]:
    """清除交易记录或资金记录。body.category: 'trading' | 'fund'。"""
    category = (body.get("category") or "").strip()
    if category not in ("trading", "fund"):
        raise HTTPException(status_code=400, detail="category 必须是 'trading' 或 'fund'")
    try:
        return deps.paper.clear_records(category)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/paper/risk-blocks")
def paper_risk_blocks(limit: int = 40) -> Dict[str, Any]:
    """最近 risk_block 流水（供标注有效率）。"""
    return deps.paper.list_risk_blocks(limit=limit)


@router.post("/api/paper/risk-blocks/annotate")
def paper_risk_block_annotate(body: dict) -> Dict[str, Any]:
    """标注 risk_block.meta.outcome = true_positive|false_positive|unknown|clear。"""
    outcome = str((body or {}).get("outcome") or "").strip()
    if not outcome:
        raise HTTPException(status_code=400, detail="outcome 必填")
    try:
        out = deps.paper.annotate_risk_block(
            outcome=outcome,
            index=(body or {}).get("index"),
            ts=(body or {}).get("ts"),
            note=str((body or {}).get("note") or ""),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error") or "标注失败")
    return out
