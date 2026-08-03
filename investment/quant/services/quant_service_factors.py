"""QuantService · 因子面板 / IC / OLS / 权重与阈值（进阶）；``run_cross_section`` 属 ② 回溯。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


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
        from core.ports.market import resolve_market_code
        from skills.index.engine import default_benchmark, fetch_index_bars

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
        from core.ports.market import resolve_market_code
        from skills.index.engine import default_benchmark, fetch_index_bars

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
        from core.ports.market import resolve_market_code
        from skills.index.engine import default_benchmark, fetch_index_bars

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
        from core.ports.market import resolve_market_code
        from core.watching_store import read_watching
        from skills.index.engine import default_benchmark, fetch_index_bars

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
        pit_fundamentals: bool = False,
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
    ) -> Dict[str, Any]:
        """研究池：β 聚类 → 组权 → 组内 OOS → 分组 score → 分池合成（不写 config）。

        默认：complete-linkage + τ 切树；β 因子维 z-score；关 PIT。
        """
        from quant.research.factor_ols_clusters import (
            compute_factor_ols_cluster_report,
            merge_cluster_universe,
        )
        from quant.research.cluster_oos import attach_cluster_oos_gates
        from quant.research.cluster_group_score import attach_cluster_group_scores
        from quant.research.cluster_pool_merge import attach_cluster_pool_merge
        from quant.research.cluster_pool_artifact import attach_cluster_pool_artifact
        from quant.research.cluster_multi_score import attach_cluster_multi_score
        from core.data_service import bars_and_source, get_quote
        from core.ports.market import resolve_market_code
        from skills.index.engine import default_benchmark, fetch_index_bars

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

        index_cache: Dict[str, Any] = {}
        panels: List[Dict[str, Any]] = []
        bars_by_code: Dict[str, Any] = {}
        quotes_by_code: Dict[str, Any] = {}
        # 规范化后的宇宙（与面板 / code_map 键一致）
        resolved_codes: List[str] = []
        resolved_seen: set = set()
        watching_resolved: List[str] = []
        holdings_resolved: List[str] = []
        holdings_added_resolved: List[str] = []
        code_roles: Dict[str, Any] = dict(uni_meta.get("code_roles") or {})

        for code in codes:
            role = code_roles.get(code) or {}
            quote = get_quote(code)
            sym = quote.get("stock_code") if quote.get("success") else code
            sym_s = str(sym)
            if quote.get("success"):
                quotes_by_code[sym_s] = quote
            if sym_s not in resolved_seen:
                resolved_seen.add(sym_s)
                resolved_codes.append(sym_s)
            if role.get("from_watching") and sym_s not in watching_resolved:
                watching_resolved.append(sym_s)
            if role.get("from_holdings") and sym_s not in holdings_resolved:
                holdings_resolved.append(sym_s)
            if role.get("holdings_added") and sym_s not in holdings_added_resolved:
                holdings_added_resolved.append(sym_s)
            bars, src = bars_and_source(code, limit=lookback + 35)
            if not bars and quote.get("success"):
                bars, src = bars_and_source(sym, limit=lookback + 35)
            if not bars:
                panels.append({"code": sym_s, "bars": []})
                continue
            bars_by_code[sym_s] = bars
            market, _ = resolve_market_code(code)
            bench = default_benchmark(market)
            if bench not in index_cache:
                index_cache[bench], _ = fetch_index_bars(bench, limit=lookback + 35)
            # 分组探针默认不拉财务：PIT/快照都会显著拖慢
            panels.append(
                {
                    "code": sym_s,
                    "bars": bars,
                    "index_bars": index_cache.get(bench) or None,
                    "fundamentals": None,
                    "data_source": src,
                }
            )

        report = compute_factor_ols_cluster_report(
            panels,
            horizon_days=horizon_days,
            ridge_lambda=ridge_lambda,
            n_clusters=n_clusters,
            pit_fundamentals=bool(pit_fundamentals),
            l2_normalize_betas=l2_normalize_betas,
            beta_scale=str(beta_scale or "feature_zscore"),
            cluster_method=str(cluster_method or "hierarchical"),
            cluster_linkage=str(cluster_linkage or "complete"),
            within_dist_quantile=float(within_dist_quantile or 0.75),
        )
        report["task"] = "factor_ols_clusters"
        report["lookback"] = lookback
        report["watching_limit"] = limit
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
        return report

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
        self, mode: str, *, enabled: Optional[bool] = None
    ) -> Dict[str, Any]:
        from core.signal.cluster_live import set_cluster_scoring_mode

        return set_cluster_scoring_mode(mode, enabled=enabled)

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
    ) -> Dict[str, Any]:
        """一键：晋升 + 影子/激活 + 刷新分池簿。"""
        from core.signal.cluster_live import apply_cluster_live_shortcut

        return apply_cluster_live_shortcut(
            artifact,
            from_draft=from_draft,
            note=note,
            mode=mode,
            refresh_book=True,
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

    def run_next_day_trend(
        self,
        *,
        lookback: int = 120,
        lookback_eval_days: int = 60,
        flat_band_pct: float = 0.5,
        watching_limit: int = 20,
        codes: Optional[List[str]] = None,
        pit_fundamentals: bool = True,
    ) -> Dict[str, Any]:
        """观察池日频+1 趋势探针：收盘→次日方向；不写 config。"""
        from core.backtest.next_day_trend import compute_next_day_trend_report
        from core.data_service import bars_and_source, get_quote
        from core.watching_store import read_watching

        if codes:
            use_codes = [str(c).strip() for c in codes if str(c).strip()]
        else:
            uni = read_watching()
            use_codes = list(uni.get("watchlist") or [])
        limit = max(1, min(int(watching_limit or 20), 40))
        use_codes = use_codes[:limit]
        if not use_codes:
            return {
                "success": False,
                "ok": False,
                "error": "观察池为空；请先在数据中心加票",
                "task": "next_day_trend",
                "mode": "watchlist_daily_plus1",
                "horizon_days": 1,
            }

        stock_bars: Dict[str, Any] = {}
        live_quotes: Dict[str, Any] = {}
        for code in use_codes:
            quote = get_quote(code)
            sym = quote.get("stock_code") if quote.get("success") else code
            bars, _src = bars_and_source(code, limit=lookback + 20)
            if not bars and quote.get("success"):
                bars, _src = bars_and_source(sym, limit=lookback + 20)
            key = str(sym)
            if bars:
                stock_bars[key] = bars
            if quote.get("success"):
                live_quotes[key] = quote

        if not stock_bars:
            return {
                "success": False,
                "ok": False,
                "error": "无法拉取观察池日线",
                "task": "next_day_trend",
                "mode": "watchlist_daily_plus1",
                "horizon_days": 1,
            }

        report = compute_next_day_trend_report(
            stock_bars,
            flat_band_pct=flat_band_pct,
            lookback_eval_days=lookback_eval_days,
            pit_fundamentals=pit_fundamentals,
            live_quotes=live_quotes,
        )
        report["task"] = "next_day_trend"
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
        return suggestion

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
