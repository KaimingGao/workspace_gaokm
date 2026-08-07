"""QuantService · 因子面板 / IC / OLS / 权重与阈值（进阶）；``run_cross_section`` 属 ② 回溯。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class QuantFactorMixin:
    def list_factors(self) -> Dict[str, Any]:
        from core.signal.factor_panel import build_factor_panel

        return build_factor_panel()

    def build_factor_panel(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
        horizon_days: int = 3,
        with_experiment: bool = False,
    ) -> Dict[str, Any]:
        from core.signal.factor_panel import build_factor_panel

        if not with_experiment:
            return build_factor_panel()

        exp = self.run_factor_experiment(code, lookback=lookback, horizon_days=horizon_days)
        if not exp.get("success"):
            return exp
        panel = build_factor_panel(
            experiment=exp,
            stock_code=exp.get("stock_code") or code,
            horizon_days=exp.get("horizon_days") or horizon_days,
            data_source=exp.get("data_source"),
        )
        panel["experiment"] = {
            "horizon_days": exp.get("horizon_days"),
            "factor_count": exp.get("factor_count"),
        }
        return panel

    def run_cross_section(
        self,
        *,
        codes: Optional[List[str]] = None,
        limit: int = 10,
        min_score: Optional[float] = None,
        horizon_days: int = 3,
    ) -> Dict[str, Any]:
        from core.signal.cross_section import rank_cross_section

        return rank_cross_section(
            codes,
            horizon_days=horizon_days,
            limit=limit,
            min_score=min_score,
        )

    def run_factor_report(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
    ) -> Dict[str, Any]:
        from quant.research.factor_report import compute_factor_ic_report
        from core.data_service import bars_and_source, get_quote
        from core.ports.market import default_benchmark, fetch_index_bars, resolve_market_code

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars:
            return {"success": False, "error": f"无法获取 {code} 日线"}

        market, _ = resolve_market_code(code)
        index_bars, _ = fetch_index_bars(default_benchmark(market), limit=lookback + 35)
        fundamentals = self._experiment_fundamentals(code, sym)
        report = compute_factor_ic_report(
            bars,
            index_bars=index_bars or None,
            fundamentals=fundamentals,
            stock_code=str(sym or code),
            pit_fundamentals=True,
        )
        report["stock_code"] = sym
        report["data_source"] = src
        return report

    def _experiment_fundamentals(self, code: str, sym: Optional[str] = None) -> Optional[dict]:
        from core.signal.config import load_signal_config
        from core.signal.fundamentals_bridge import fetch_score_fundamentals

        cfg = load_signal_config()
        fund_cfg = cfg.get("fundamentals") or {}
        if not fund_cfg.get("enabled", True):
            return None
        if not fund_cfg.get("use_in_ic_experiment", True):
            return None
        return fetch_score_fundamentals(code) or (
            fetch_score_fundamentals(sym) if sym else None
        )

    def run_factor_experiment(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
        horizon_days: int = 3,
    ) -> Dict[str, Any]:
        from core.signal.factor_registry import run_factor_experiment
        from core.data_service import bars_and_source, get_quote
        from core.ports.market import default_benchmark, fetch_index_bars, resolve_market_code

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars:
            return {"success": False, "error": f"无法获取 {code} 日线"}

        market, _ = resolve_market_code(code)
        index_bars, _ = fetch_index_bars(default_benchmark(market), limit=lookback + 35)
        fundamentals = self._experiment_fundamentals(code, sym)
        report = run_factor_experiment(
            bars,
            horizon_days=horizon_days,
            index_bars=index_bars or None,
            fundamentals=fundamentals,
            stock_code=str(sym or code),
            pit_fundamentals=True,
        )
        report["stock_code"] = sym
        report["data_source"] = src
        from core.signal.factor_panel import build_factor_panel

        report["panel"] = build_factor_panel(
            experiment=report,
            stock_code=sym,
            horizon_days=report.get("horizon_days") or horizon_days,
            data_source=src,
        )
        return report

    def run_factor_ols_experiment(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
        horizon_days: int = 3,
        ridge_lambda: float = 0.0,
    ) -> Dict[str, Any]:
        from quant.research.factor_ols import compute_factor_ols_report
        from core.data_service import bars_and_source, get_quote
        from core.ports.market import default_benchmark, fetch_index_bars, resolve_market_code

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars:
            return {"success": False, "error": f"无法获取 {code} 日线", "task": "factor_ols"}

        market, _ = resolve_market_code(code)
        index_bars, _ = fetch_index_bars(default_benchmark(market), limit=lookback + 35)
        fundamentals = self._experiment_fundamentals(code, sym)
        report = compute_factor_ols_report(
            bars,
            horizon_days=horizon_days,
            index_bars=index_bars or None,
            fundamentals=fundamentals,
            stock_code=str(sym or code),
            pit_fundamentals=True,
            ridge_lambda=ridge_lambda,
        )
        report["stock_code"] = sym
        report["data_source"] = src
        report["task"] = "factor_ols"
        return report

    def run_factor_ols_pool_experiment(
        self,
        *,
        lookback: int = 120,
        horizon_days: int = 3,
        watching_limit: int = 8,
        ridge_lambda: float = 0.0,
    ) -> Dict[str, Any]:
        """研究池多票堆叠时序 OLS（显式触发；不写 config）。"""
        from quant.research.factor_ols import compute_factor_ols_pooled_report
        from core.data_service import bars_and_source, get_quote
        from core.ports.market import default_benchmark, fetch_index_bars, resolve_market_code
        from core.watching_store import read_watching

        uni = read_watching()
        codes = list(uni.get("watchlist") or [])
        limit = max(2, min(int(watching_limit or 8), 20))
        codes = codes[:limit]
        if len(codes) < 2:
            return {
                "success": False,
                "error": "研究池至少 2 只才可跑池内 OLS",
                "task": "factor_ols_pool",
                "mode": "watching_pooled",
                "stock_count": len(codes),
            }

        panels: List[Dict[str, Any]] = []
        for code in codes:
            quote = get_quote(code)
            sym = quote.get("stock_code") if quote.get("success") else code
            bars, src = bars_and_source(code, limit=lookback + 35)
            if not bars and quote.get("success"):
                bars, src = bars_and_source(sym, limit=lookback + 35)
            if not bars:
                panels.append({"code": str(sym), "bars": []})
                continue
            market, _ = resolve_market_code(code)
            index_bars, _ = fetch_index_bars(
                default_benchmark(market), limit=lookback + 35
            )
            fundamentals = self._experiment_fundamentals(code, sym)
            panels.append(
                {
                    "code": str(sym),
                    "bars": bars,
                    "index_bars": index_bars or None,
                    "fundamentals": fundamentals,
                    "data_source": src,
                }
            )

        report = compute_factor_ols_pooled_report(
            panels, horizon_days=horizon_days, ridge_lambda=ridge_lambda
        )
        report["task"] = "factor_ols_pool"
        report["lookback"] = lookback
        report["watching_limit"] = limit
        return report

    def run_factor_ols_cluster_experiment(
        self,
        *,
        lookback: int = 80,
        horizon_days: int = 3,
        watching_limit: int = 8,
        ridge_lambda: float = 0.0,
        n_clusters: Optional[int] = None,
        pit_fundamentals: bool = True,
        sentiment_pit: bool = False,
        l2_normalize_betas: Optional[bool] = None,
        beta_scale: str = "feature_zscore",
        cluster_method: str = "hierarchical",
        cluster_linkage: str = "complete",
        within_dist_quantile: float = 0.75,
        run_oos_gate: bool = True,
        oos_tol_pp: float = 1.0,
        run_group_score: bool = True,
        run_pool_merge: bool = True,
        top_n_per_group: int = 10,
        respect_regime: bool = True,
        select_ridge: bool = True,
        collinearity_policy: str = "drop_redundant",
        progress_cb: Optional[Any] = None,
        refresh_bars: bool = True,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """研究池：β 聚类 → 组权 → 组内 OOS → 分组 score → 分池合成（不写 config）。

        默认：complete-linkage + τ 切树；β 因子维 z-score；PIT 默认开（FH5）；
        B5 respect_regime；B3 选 λ + drop_redundant。
        refresh_bars 默认 True：过期/缺条日线限流拉网（约 36h 内仍复用）；False=纯缓存重算。
        P1 use_cache：refresh_bars=False 时默认启用 24h 报告缓存（watchlist+参数指纹未变即复用），
        避免重复 OLS/OOS/分池计算；refresh_bars=True 强制跳过缓存。
        """
        from quant.research.factor_ols_clusters import (
            compute_factor_ols_cluster_report,
            merge_cluster_universe,
        )
        from quant.research.cluster_panels import build_cluster_ols_panels
        from quant.research.cluster_oos import attach_cluster_oos_gates
        from quant.research.cluster_group_score import attach_cluster_group_scores
        from quant.research.cluster_pool_merge import attach_cluster_pool_merge
        from quant.research.cluster_pool_artifact import attach_cluster_pool_artifact
        from quant.research.cluster_multi_score import attach_cluster_multi_score

        holdings_raw: List[Any] = []
        try:
            from core.paths import PAPER_PATH
            from core.paper import load_paper
            import os as _os

            if _os.path.isfile(PAPER_PATH):
                holdings_raw = list(load_paper(PAPER_PATH).get("holdings") or [])
        except Exception:
            holdings_raw = []

        watchlist: List[Any] = []
        try:
            from core.watching_store import read_watching

            watchlist = list((read_watching() or {}).get("watchlist") or [])
        except Exception:
            watchlist = []

        # P1：24h 报告缓存（refresh_bars=False 且 use_cache=True 时启用）
        cache_enabled = use_cache and not bool(refresh_bars)
        cache_fp = None
        if cache_enabled:
            cache_fp = _cluster_cache_fingerprint(
                watchlist,
                lookback=lookback,
                horizon_days=horizon_days,
                n_clusters=n_clusters,
                ridge_lambda=ridge_lambda,
                pit_fundamentals=pit_fundamentals,
                l2_normalize_betas=l2_normalize_betas,
                beta_scale=beta_scale,
                cluster_method=cluster_method,
                cluster_linkage=cluster_linkage,
                within_dist_quantile=within_dist_quantile,
                run_oos_gate=run_oos_gate,
                oos_tol_pp=oos_tol_pp,
                run_group_score=run_group_score,
                run_pool_merge=run_pool_merge,
                top_n_per_group=top_n_per_group,
                respect_regime=respect_regime,
                select_ridge=select_ridge,
                collinearity_policy=collinearity_policy,
            )
            cached = _load_cluster_cache(cache_fp, max_age_hours=24)
            if cached is not None:
                if progress_cb:
                    try:
                        progress_cb("命中 24h 缓存，直接复用", 1, 1)
                    except Exception:
                        logger.warning("因子服务处理异常", exc_info=True)
                cached["cache_hit"] = True
                cached["cache_age_hours"] = _cluster_cache_age_hours(cached)
                return cached

        # 聚类宇宙 = 全部观察池（纸面持仓仅作落地映射参考，不进聚类）
        uni_meta = merge_cluster_universe(
            watchlist,
            holdings_raw,
            watching_limit=watching_limit,
            universe_mode="watching",
        )
        codes = list(uni_meta["codes"])
        limit = int(uni_meta["watching_limit"])
        if len(codes) < 2:
            return {
                "success": False,
                "error": "观察池至少 2 只才可按 β 分组",
                "task": "factor_ols_clusters",
                "mode": "ols_beta_clusters",
                "stock_count": len(codes),
                "watching_limit": limit,
                "universe_mode": "watching",
                "watching_codes": list(uni_meta.get("watching_codes") or []),
                "holdings_codes": list(uni_meta["holdings_codes"]),
                "holdings_added": list(uni_meta["holdings_added"]),
                "universe_count": int(uni_meta["universe_count"]),
            }

        n_codes = len(codes)

        def _on_progress(msg: str, cur: int = 0, tot: int = 0) -> None:
            if not progress_cb:
                return
            try:
                progress_cb(msg, int(cur or 0), int(tot or n_codes or 1))
            except Exception:
                pass

        _on_progress(f"拉日线 0/{n_codes}", 0, n_codes)
        built = build_cluster_ols_panels(
            codes,
            lookback=lookback,
            pit_fundamentals=bool(pit_fundamentals),
            code_roles=dict(uni_meta.get("code_roles") or {}),
            progress_cb=_on_progress,
            refresh_bars=bool(refresh_bars),
        )
        panels = list(built.get("panels") or [])
        bars_by_code = dict(built.get("bars_by_code") or {})
        quotes_by_code = dict(built.get("quotes_by_code") or {})
        resolved_codes = list(built.get("resolved_codes") or [])
        watching_resolved = list(built.get("watching_resolved") or [])
        holdings_resolved = list(built.get("holdings_resolved") or [])
        holdings_added_resolved = list(built.get("holdings_added_resolved") or [])
        bars_refresh = dict(built.get("bars_refresh") or {})

        _on_progress(f"拟合 0/{n_codes}", 0, n_codes)
        report = compute_factor_ols_cluster_report(
            panels,
            horizon_days=horizon_days,
            ridge_lambda=ridge_lambda,
            n_clusters=n_clusters,
            pit_fundamentals=bool(pit_fundamentals),
            sentiment_pit=bool(sentiment_pit),
            l2_normalize_betas=l2_normalize_betas,
            beta_scale=str(beta_scale or "feature_zscore"),
            cluster_method=str(cluster_method or "hierarchical"),
            cluster_linkage=str(cluster_linkage or "complete"),
            within_dist_quantile=float(within_dist_quantile or 0.75),
            respect_regime=bool(respect_regime),
            select_ridge=bool(select_ridge),
            collinearity_policy=str(collinearity_policy or "drop_redundant"),
            # P3：拟合并发对齐面板拉取（12），cold-cache 重算时缩短 CPU 段
            max_workers=12,
            progress_cb=_on_progress,
        )
        report["task"] = "factor_ols_clusters"
        report["lookback"] = lookback
        report["watching_limit"] = limit
        report["refresh_bars"] = bool(refresh_bars)
        report["bars_refresh"] = bars_refresh
        report["universe_mode"] = "watching"
        # 统计以观察池宇宙为准；解析失败时回退原始列表，避免 UI 显示 0
        report["watching_codes"] = watching_resolved or list(
            uni_meta.get("watching_codes") or []
        )
        report["holdings_codes"] = holdings_resolved or list(
            uni_meta["holdings_codes"]
        )
        report["holdings_added"] = holdings_added_resolved or list(
            uni_meta["holdings_added"]
        )
        report["universe_count"] = int(uni_meta["universe_count"])
        report["universe_codes"] = resolved_codes or list(uni_meta["codes"])
        report["holdings_raw_count"] = len(holdings_raw)
        report["universe_note"] = (
            f"宇宙=观察池全部 {report['universe_count']} 只"
        )
        # 成员展示用：报价/解析到的中文名（研究枢纽未拉观察名单时也能显示）
        name_by_code: Dict[str, str] = {}
        for code_key, q in (quotes_by_code or {}).items():
            if not isinstance(q, dict):
                continue
            c = str(code_key or "").strip()
            nm = str(q.get("stock_name") or q.get("name") or "").strip()
            if c and nm:
                name_by_code[c] = nm
        report["name_by_code"] = name_by_code
        if report.get("success"):
            _on_progress("OOS / 分池…", n_codes, n_codes)
            attach_cluster_oos_gates(
                report,
                lookback=lookback,
                horizon_days=horizon_days,
                oos_tol_pp=float(oos_tol_pp),
                run_oos_gate=bool(run_oos_gate),
            )
            attach_cluster_group_scores(
                report,
                bars_by_code,
                horizon_days=horizon_days,
                quotes_by_code=quotes_by_code,
                run_group_score=bool(run_group_score),
            )
            if run_group_score:
                attach_cluster_pool_merge(
                    report,
                    bars_by_code,
                    horizon_days=horizon_days,
                    top_n_per_group=int(top_n_per_group or 10),
                    run_pool_merge=bool(run_pool_merge),
                )
                attach_cluster_pool_artifact(report)
                attach_cluster_multi_score(
                    report,
                    bars_by_code,
                    quotes_by_code=quotes_by_code,
                    horizon_days=horizon_days,
                )
                # L3：重聚类结果进草稿（≠ active），待人审 promote
                try:
                    from core.signal.cluster_live import save_cluster_draft

                    art = report.get("pool_artifact") or {}
                    if art.get("success") and art.get("code_map"):
                        save_cluster_draft(art)
                        report["cluster_draft_saved"] = True
                except Exception:
                    report["cluster_draft_saved"] = False
        flags = dict(built.get("lookahead_flags") or {})
        if report.get("large_universe") and report.get("daily_pit") is False:
            flags = dict(flags)
            flags["daily_pit"] = False
            flags["speed_note"] = report.get("speed_note")
            if flags.get("fundamentals") == "pit_as_of":
                flags["fundamentals"] = "pit_snapshot"
                flags["note"] = (
                    str(flags.get("note") or "")
                    + " 大宇宙加速：拟合用末日财务快照（非逐日 PIT）。"
                ).strip()
        report["lookahead_flags"] = flags
        report["pit_fundamentals"] = bool(pit_fundamentals)
        report["fundamentals_pit_summary"] = flags.get("pit_summary")
        # P1：成功则落盘缓存（refresh_bars=False 路径）
        if cache_enabled and cache_fp and report.get("success"):
            _save_cluster_cache(report, cache_fp)
        return report

    def start_factor_ols_cluster_job(self, **kwargs: Any) -> Dict[str, Any]:
        """FH2：后台跑分组 OLS；轮询 ``GET /api/jobs/quant-ols-clusters``。"""
        import threading

        from core.job_progress import quant_ols_clusters_job
        from quant.research.factor_ols_clusters import clamp_watching_limit

        # 轮询/重开前先回收陈旧任务
        quant_ols_clusters_job.reclaim_if_stale()

        if quant_ols_clusters_job.is_running():
            stale = quant_ols_clusters_job.stale_seconds()
            job_snap = quant_ols_clusters_job.get()
            msg = str((job_snap or {}).get("message") or "")
            stuck_at_start = "拉日线 0/" in msg or msg in (
                "排队中…",
                "启动中…",
                "合并宇宙…",
            )
            if stale is not None and (
                stale >= 180 or (stuck_at_start and stale >= 90)
            ):
                quant_ols_clusters_job.force_fail(
                    f"分组任务无进展已 {int(stale)}s，已自动释放（{msg or 'running'}）"
                )
            else:
                return {
                    "ok": False,
                    "success": False,
                    "error": "已有分组任务在运行",
                    "job": quant_ols_clusters_job.get(),
                }

        # 进度按「票数」量级：尊重 watching_limit，避免按百票满池估 total
        try:
            from core.watching_store import read_watching

            n_watch_all = len(list((read_watching() or {}).get("watchlist") or []))
        except Exception:
            n_watch_all = 20
        watch_limit = clamp_watching_limit(kwargs.get("watching_limit") or 12, 12)
        n_watch = min(n_watch_all, watch_limit) if n_watch_all else watch_limit
        job_total = max(20, n_watch * 2 + 10)

        job_id = quant_ols_clusters_job.start(
            kind="factor_ols_clusters",
            total=job_total,
            message="排队中…",
        )

        def _progress(msg: str, cur: int = 0, tot: int = 0) -> None:
            # 映射到 job：前 80% = 拉日线+拟合；后 20% = OOS/分池
            t = max(1, int(tot or n_watch or 1))
            c = max(0, int(cur or 0))
            if "OOS" in msg or "分池" in msg or "组池" in msg or "聚类" in msg:
                mapped = int(job_total * 0.8) + min(
                    int(job_total * 0.2) - 1, max(1, c)
                )
            elif "拟合" in msg:
                mapped = int(job_total * 0.4) + int((job_total * 0.4) * min(1.0, c / t))
            else:
                # 拉日线
                mapped = int((job_total * 0.4) * min(1.0, c / t))
            quant_ols_clusters_job.update(
                current=max(1, min(job_total - 1, mapped)),
                total=job_total,
                message=msg,
                job_id=job_id,
            )

        def _worker() -> None:
            try:
                if quant_ols_clusters_job.is_cancel_requested():
                    quant_ols_clusters_job.finish(error="已取消", job_id=job_id)
                    return
                quant_ols_clusters_job.update(
                    current=1,
                    total=job_total,
                    message=f"合并宇宙… {n_watch} 只",
                    job_id=job_id,
                )
                if quant_ols_clusters_job.is_cancel_requested():
                    quant_ols_clusters_job.finish(error="已取消", job_id=job_id)
                    return
                result = self.run_factor_ols_cluster_experiment(
                    progress_cb=_progress,
                    **{k: v for k, v in kwargs.items() if k != "progress_cb"},
                )
                if quant_ols_clusters_job.is_cancel_requested():
                    quant_ols_clusters_job.finish(error="已取消", job_id=job_id)
                    return
                if not result.get("success"):
                    quant_ols_clusters_job.finish(
                        error=str(result.get("error") or "分组失败"),
                        result=result,
                        job_id=job_id,
                    )
                    return
                quant_ols_clusters_job.finish(result=result, job_id=job_id)
            except Exception as e:
                quant_ols_clusters_job.finish(error=str(e), job_id=job_id)

        threading.Thread(
            target=_worker, name=f"ols-clusters-{job_id}", daemon=True
        ).start()
        return {
            "ok": True,
            "success": True,
            "background": True,
            "job": quant_ols_clusters_job.get(),
        }

    def run_cluster_multi_score(
        self,
        *,
        artifact: Optional[Dict[str, Any]] = None,
        lookback: int = 80,
        horizon_days: int = 3,
        watching_limit: int = 20,
    ) -> Dict[str, Any]:
        """用归档 code_map 对研究池多权复打分（不写 config）。"""
        from quant.research.cluster_multi_score import run_multi_score_from_artifact

        return run_multi_score_from_artifact(
            artifact=artifact,
            lookback=lookback,
            horizon_days=horizon_days,
            watching_limit=watching_limit,
        )

    def cluster_live_status(
        self,
        *,
        audit_rotate: bool = False,
        audit_offset: Optional[int] = None,
    ) -> Dict[str, Any]:
        from core.signal.cluster_live import cluster_status_public

        return cluster_status_public(
            audit_rotate=bool(audit_rotate),
            audit_offset=audit_offset,
        )

    def promote_cluster_live(
        self,
        artifact: Optional[Dict[str, Any]] = None,
        *,
        note: str = "",
        force: bool = False,
        from_draft: bool = False,
    ) -> Dict[str, Any]:
        from core.signal.cluster_live import (
            load_cluster_draft,
            promote_cluster_artifact,
        )

        art = artifact
        if from_draft or not art:
            art = load_cluster_draft() or art
        if not art:
            return {"success": False, "error": "无产物可晋升（传 artifact 或先存草稿）"}
        return promote_cluster_artifact(art, note=note, force=force)

    def rollback_cluster_live(self, *, to_version: Optional[int] = None) -> Dict[str, Any]:
        from core.signal.cluster_live import rollback_cluster_weights

        return rollback_cluster_weights(to_version=to_version)

    def set_cluster_live_mode(
        self,
        mode: str,
        *,
        enabled: Optional[bool] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        from core.signal.cluster_live import set_cluster_scoring_mode

        return set_cluster_scoring_mode(mode, enabled=enabled, force=force)

    def save_cluster_live_draft(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        from core.signal.cluster_live import save_cluster_draft

        return save_cluster_draft(artifact or {})

    def refresh_cluster_live_book(self) -> Dict[str, Any]:
        from core.signal.cluster_live import refresh_cluster_book_daily

        return refresh_cluster_book_daily()

    def rank_cluster_live_pools(
        self,
        *,
        top_n_per_group: Optional[int] = None,
        max_names: Optional[int] = None,
    ) -> Dict[str, Any]:
        from core.signal.cluster_rank import rank_cluster_pools

        return rank_cluster_pools(
            None,
            top_n_per_group=top_n_per_group,
            max_names=max_names,
            persist_book=True,
        )

    def apply_cluster_live_shortcut(
        self,
        artifact: Optional[Dict[str, Any]] = None,
        *,
        from_draft: bool = True,
        note: str = "",
        mode: str = "shadow",
        force: bool = False,
    ) -> Dict[str, Any]:
        """一键：晋升 + 影子/激活 + 刷新分池簿。"""
        from core.signal.cluster_live import apply_cluster_live_shortcut

        return apply_cluster_live_shortcut(
            artifact,
            from_draft=from_draft,
            note=note,
            mode=mode,
            refresh_book=True,
            force=force,
        )

    def preview_cluster_paper_rebalance(
        self,
        book: List[Dict[str, Any]],
        *,
        top_k: Optional[int] = None,
        confirm: bool = False,
        artifact: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """分池候选簿 → 纸面调仓预演或确认落账（confirm 才写 paper，永不写 config）。"""
        from quant.research.cluster_pool_artifact import preview_paper_pool_rebalance

        return preview_paper_pool_rebalance(
            book or [],
            top_k=top_k,
            dry_run=not bool(confirm),
            confirm=bool(confirm),
            artifact=artifact,
        )

    def run_factor_cs_ic_experiment(
        self,
        *,
        lookback: int = 120,
        horizon_days: int = 3,
        watching_limit: int = 12,
        min_names: int = 5,
        pit_fundamentals: bool = True,
    ) -> Dict[str, Any]:
        """研究池逐因子日频截面 IC（S1；显式触发，不写 config）。"""
        from core.backtest.factor_cs_ic import compute_factor_cross_section_ic
        from core.data_service import bars_and_source, get_quote
        from core.watching_store import read_watching

        uni = read_watching()
        codes = list(uni.get("watchlist") or [])
        limit = max(3, min(int(watching_limit or 12), 30))
        codes = codes[:limit]
        if len(codes) < 3:
            return {
                "success": False,
                "ok": False,
                "error": "研究池至少 3 只才可跑因子截面 IC",
                "task": "factor_cs_ic",
                "stock_count": len(codes),
            }

        stock_bars: Dict[str, Any] = {}
        for code in codes:
            quote = get_quote(code)
            sym = quote.get("stock_code") if quote.get("success") else code
            bars, _src = bars_and_source(code, limit=lookback + 35)
            if not bars and quote.get("success"):
                bars, _src = bars_and_source(sym, limit=lookback + 35)
            if bars:
                stock_bars[str(sym)] = bars

        if len(stock_bars) < 3:
            return {
                "success": False,
                "ok": False,
                "error": "有效日线不足 3 只",
                "task": "factor_cs_ic",
                "stock_count": len(stock_bars),
            }

        report = compute_factor_cross_section_ic(
            stock_bars,
            horizon_days=horizon_days,
            min_names=min_names,
            pit_fundamentals=pit_fundamentals,
        )
        report["task"] = "factor_cs_ic"
        report["lookback"] = lookback
        report["watching_limit"] = limit
        report["codes"] = list(stock_bars.keys())
        return report

    def suggest_weights(self, code: str = "茅台", **kwargs: Any) -> Dict[str, Any]:
        from core.signal.weight_suggest import format_weight_config_diff, suggest_weights_from_ic

        lookback = int(kwargs.get("lookback") or 120)
        horizon_days = int(kwargs.get("horizon_days") or 3)
        use_cs_ic = bool(kwargs.get("use_cs_ic", True))
        watching_limit = int(kwargs.get("watching_limit") or 12)

        ic_mode = "single"
        exp: Dict[str, Any]
        cs_ic: Optional[Dict[str, Any]] = None
        if use_cs_ic:
            cs_ic = self.run_factor_cs_ic_experiment(
                lookback=lookback,
                horizon_days=horizon_days,
                watching_limit=watching_limit,
            )
            if cs_ic.get("success") and (cs_ic.get("factors") or []):
                exp = cs_ic
                ic_mode = "cs_ic"
            else:
                exp = self.run_factor_experiment(
                    code, lookback=lookback, horizon_days=horizon_days
                )
                ic_mode = "single"
        else:
            exp = self.run_factor_experiment(
                code, lookback=lookback, horizon_days=horizon_days
            )

        if not exp.get("success"):
            return exp

        ols = self.run_factor_ols_experiment(
            code,
            lookback=lookback,
            horizon_days=horizon_days,
            ridge_lambda=float(kwargs.get("ridge_lambda") or 0.0),
        )
        corr = self.run_factor_corr(codes=None, limit=30, horizon_days=horizon_days)
        suggestion = suggest_weights_from_ic(
            exp,
            ols_report=ols if ols.get("success") else None,
            corr_report=corr if corr.get("success") else None,
            ic_mode=ic_mode,
        )
        run_oos_gate = bool(kwargs.get("run_oos_gate", True))
        oos_gate: Optional[Dict[str, Any]] = None
        if run_oos_gate and suggestion.get("success"):
            from core.signal.weight_oos_gate import evaluate_weight_suggestion_oos

            oos_gate = evaluate_weight_suggestion_oos(
                suggestion.get("current_weights") or {},
                suggestion.get("suggested_weights") or {},
                lookback=min(lookback, 90),
                top_k=int(kwargs.get("oos_top_k") or 3),
                horizon_days=horizon_days,
                watching_limit=min(watching_limit, 10),
                oos_tol_pp=float(kwargs.get("oos_tol_pp") or 1.0),
            )
            suggestion["oos_gate"] = oos_gate
            suggestion["promote_ready"] = bool(
                oos_gate.get("ok") and oos_gate.get("passed") and not oos_gate.get("skipped")
            )
        else:
            suggestion["oos_gate"] = {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "gate_disabled",
            }
            suggestion["promote_ready"] = False

        suggestion["factor_experiment"] = exp
        suggestion["factor_cs_ic"] = cs_ic
        suggestion["factor_ols"] = ols
        suggestion["factor_corr"] = corr
        suggestion["config_diff"] = format_weight_config_diff(suggestion)
        # FS1：趋势族共线提示挂到晋升建议
        try:
            from core.signal.factor_collinearity import trend_family_collinearity

            rows = []
            for it in (corr.get("items") or corr.get("rows") or []):
                if isinstance(it, dict) and (it.get("sub_scores") or it.get("factors")):
                    rows.append(it.get("sub_scores") or it.get("factors"))
            if len(rows) >= 3:
                suggestion["trend_collinearity"] = trend_family_collinearity(rows)
        except Exception:
            pass
        return suggestion

    def run_alt_sentiment_ic(
        self,
        *,
        lookback: int = 80,
        horizon_days: int = 3,
        watching_limit: int = 8,
        pit_fundamentals: bool = True,
    ) -> Dict[str, Any]:
        """FS2：观察池 alt_sentiment as_of TS IC（研究只读；不改 live 闸）。"""
        from core.data_service import bars_and_source, get_quote
        from core.research.sentiment_ic import summarize_alt_sentiment_ic_pool
        from core.watching_store import read_watching

        uni = read_watching()
        codes = list(uni.get("watchlist") or [])[: max(1, min(int(watching_limit or 8), 20))]
        panels = []
        for code in codes:
            quote = get_quote(code)
            sym = quote.get("stock_code") if quote.get("success") else code
            bars, _src = bars_and_source(code, limit=lookback + 35)
            if not bars and quote.get("success"):
                bars, _src = bars_and_source(sym, limit=lookback + 35)
            if bars:
                panels.append({"code": str(sym), "bars": bars})
        out = summarize_alt_sentiment_ic_pool(
            panels,
            horizon_days=horizon_days,
            pit_fundamentals=pit_fundamentals,
        )
        out["task"] = "alt_sentiment_ic"
        out["lookback"] = lookback
        return out

    def run_factor_corr(
        self,
        *,
        codes: Optional[List[str]] = None,
        limit: int = 30,
        horizon_days: int = 3,
    ) -> Dict[str, Any]:
        """观察池/候选截面 sub_scores 相关矩阵（研究只读）。"""
        from core.signal.cross_section import rank_cross_section
        from core.signal.factor_corr import compute_factor_corr_matrix

        ranked = rank_cross_section(
            codes,
            horizon_days=horizon_days,
            limit=max(3, min(int(limit or 30), 50)),
            min_score=0.0,
        )
        if not ranked.get("success"):
            return {
                "success": False,
                "error": ranked.get("error") or "横截面失败",
                "task": "factor_corr",
            }
        items = ranked.get("ranking") or []
        from core.signal.config import load_signal_config
        from core.signal.factor_corr import redundancy_warnings_from_corr

        report = compute_factor_corr_matrix(items)
        cfg = load_signal_config()
        if report.get("success"):
            report["redundancy_warnings"] = redundancy_warnings_from_corr(
                report,
                factor_groups=cfg.get("factor_groups") or {},
                weights=cfg.get("weights") or {},
            )
        report["task"] = "factor_corr"
        report["ranking_count"] = len(items)
        report["neutralization"] = ranked.get("neutralization")
        return report

    def suggest_thresholds(
        self,
        code: str = "茅台",
        *,
        lookback: int = 120,
        use_watching: bool = False,
        watching_limit: int = 5,
    ) -> Dict[str, Any]:
        from core.backtest.engine import scan_signal_parameters_oos
        from core.signal.threshold_suggest import (
            format_threshold_config_diff,
            suggest_stance_thresholds_from_oos,
            suggest_stance_thresholds_from_watching_oos,
        )
        from core.watching_store import read_watching
        from core.data_service import bars_and_source, get_quote

        if use_watching:
            try:
                uni = read_watching()
                codes = uni.get("watchlist") or []
            except FileNotFoundError:
                codes = []
            if len(codes) < 2:
                return {"success": False, "error": "watching watchlist 不足，无法聚合 OOS"}
            suggestion = suggest_stance_thresholds_from_watching_oos(
                codes,
                lookback=lookback,
                max_stocks=watching_limit,
            )
            if suggestion.get("success"):
                suggestion["config_diff"] = format_threshold_config_diff(suggestion)
            return suggestion

        quote = get_quote(code)
        sym = quote.get("stock_code") if quote.get("success") else code
        bars, src = bars_and_source(code, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source(sym, limit=lookback + 35)
        if not bars or len(bars) < 40:
            return {"success": False, "error": f"无法获取足够日线: {code}"}

        oos = scan_signal_parameters_oos(
            bars,
            min_scores=[45, 50, 55, 60, 65],
            horizon_days_list=[2, 3],
        )
        suggestion = suggest_stance_thresholds_from_oos(oos)
        suggestion["stock_code"] = sym
        suggestion["data_source"] = src
        suggestion["mode"] = "single_stock_oos"
        suggestion["oos_scan"] = oos
        suggestion["config_diff"] = format_threshold_config_diff(suggestion)
        return suggestion


# ---------- P1：分组报告 24h 缓存（watchlist+参数指纹未变即复用）----------


def _cluster_cache_fingerprint(
    watchlist: List[Any],
    *,
    lookback: int,
    horizon_days: int,
    n_clusters: Optional[int],
    ridge_lambda: float,
    pit_fundamentals: bool,
    l2_normalize_betas: Optional[bool],
    beta_scale: str,
    cluster_method: str,
    cluster_linkage: str,
    within_dist_quantile: float,
    run_oos_gate: bool,
    oos_tol_pp: float,
    run_group_score: bool,
    run_pool_merge: bool,
    top_n_per_group: int,
    respect_regime: bool,
    select_ridge: bool,
    collinearity_policy: str,
) -> str:
    """watchlist 代码（排序）+ 关键参数 → 稳定指纹；任一变化即视为需重算。"""
    import hashlib

    codes = []
    for item in watchlist or []:
        if isinstance(item, dict):
            c = str(item.get("code") or item.get("stock_code") or "").strip()
        else:
            c = str(item or "").strip()
        if c:
            codes.append(c)
    codes = sorted(set(codes))
    parts = [
        f"codes={','.join(codes)}",
        f"lb={int(lookback)}",
        f"hz={int(horizon_days)}",
        f"k={n_clusters if n_clusters is not None else 'auto'}",
        f"ridge={float(ridge_lambda):.4f}",
        f"pit={int(bool(pit_fundamentals))}",
        f"l2n={l2_normalize_betas}",
        f"bscale={beta_scale}",
        f"cmethod={cluster_method}",
        f"clink={cluster_linkage}",
        f"wdq={float(within_dist_quantile):.3f}",
        f"oos={int(bool(run_oos_gate))}",
        f"oostol={float(oos_tol_pp):.3f}",
        f"grp={int(bool(run_group_score))}",
        f"pool={int(bool(run_pool_merge))}",
        f"topn={int(top_n_per_group)}",
        f"regime={int(bool(respect_regime))}",
        f"sridge={int(bool(select_ridge))}",
        f"colpol={collinearity_policy}",
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cluster_cache_age_hours(report: Dict[str, Any]) -> Optional[float]:
    """从缓存报告里的 created_at / cached_at 估算年龄（小时）。"""
    from datetime import datetime, timezone

    ts = report.get("cache_created_at") or report.get("created_at")
    if not ts:
        return None
    try:
        if isinstance(ts, str):
            ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
        elif isinstance(ts, datetime):
            dt = ts
        else:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        return round(max(0.0, age), 2)
    except Exception:
        return None


def _load_cluster_cache(
    fingerprint: str, *, max_age_hours: int = 24
) -> Optional[Dict[str, Any]]:
    """读缓存：指纹匹配 + 年龄未超限 → 返回报告（深拷贝避免被调用方污染）。"""
    import json
    import os

    from core.paths import CLUSTER_REPORT_CACHE_PATH

    if not fingerprint or not os.path.isfile(CLUSTER_REPORT_CACHE_PATH):
        return None
    try:
        with open(CLUSTER_REPORT_CACHE_PATH, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    if str(doc.get("fingerprint") or "") != str(fingerprint):
        return None
    report = doc.get("report")
    if not isinstance(report, dict) or not report.get("success"):
        return None
    age = _cluster_cache_age_hours(doc) or 0.0
    if age > float(max_age_hours):
        return None
    # 深拷贝避免上层把缓存对象改脏
    try:
        import copy

        report = copy.deepcopy(report)
    except Exception:
        pass
    report["cache_created_at"] = doc.get("cache_created_at") or doc.get("created_at")
    return report


def _save_cluster_cache(report: Dict[str, Any], fingerprint: str) -> None:
    """落盘缓存：报告 + 指纹 + 时间戳（原子写）。"""
    from datetime import datetime, timezone

    from core.io_atomic import atomic_write_json
    from core.paths import CLUSTER_REPORT_CACHE_PATH

    if not fingerprint or not isinstance(report, dict) or not report.get("success"):
        return
    try:
        import copy

        report_copy = copy.deepcopy(report)
    except Exception:
        report_copy = report
    # 去掉进度回调残留字段（不可序列化）
    for k in ("progress_cb", "_progress_cb"):
        report_copy.pop(k, None)
    doc = {
        "fingerprint": str(fingerprint),
        "cache_created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report": report_copy,
    }
    try:
        atomic_write_json(CLUSTER_REPORT_CACHE_PATH, doc)
    except Exception:
        pass
