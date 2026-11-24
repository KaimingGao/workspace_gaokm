"""Agent × 调仓运维门面（AO 轨）：只读快照 / dry_run 预演 / 异常 / 效果建议。

不改 ŷ / stance；不自动 promote；写盘 confirm 默认关闭。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.paths import PAPER_PATH

AVAILABLE_TASKS = (
    "ops_snapshot",
    "rebalance_preview",
    "ops_anomaly",
    "feedback_draft",
    "prior_brief",
    "post_rebalance_review",
    "event_flags",
    "alpha_draft",
    "view_bundle",
)

PREVIEW_PATHS = ("holding_cycle", "watching_matrix", "sync_paper")

# 五问 / OpsSnapshot 核心键（与 build_ops_report 对齐）
OPS_SNAPSHOT_KEYS = (
    "strategy_id",
    "strategy_version",
    "cost_model",
    "data_quality",
    "risk_blocks",
    "monitor_alerts",
    "buys_blocked",
    "fallback_count",
)

_FIVE_Q = (
    ("strategy", "策略是谁", ("strategy_id", "strategy_version", "strategy_label")),
    ("cost", "成本怎么算", ("cost_model",)),
    ("data_quality", "数据质量如何", ("data_quality", "fallback_count", "source_audit")),
    ("risk", "风控拦了什么", ("risk_blocks", "buys_blocked", "risk_limits", "risk_block_items")),
    ("monitor", "监控告警", ("monitor_alerts", "monitor_metrics", "north_star")),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compact_ops_report(ops: Dict[str, Any], *, max_alerts: int = 8) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in OPS_SNAPSHOT_KEYS:
        if k in ops:
            out[k] = ops[k]
    for k in (
        "strategy_label",
        "target_weights",
        "risk_limits",
        "optimize",
        "monitor_metrics",
        "north_star",
        "source_audit",
        "exposure",
        "attribution",
    ):
        if k in ops and ops[k] is not None:
            out[k] = ops[k]
    if isinstance(out.get("monitor_alerts"), list):
        out["monitor_alerts"] = out["monitor_alerts"][:max_alerts]
    if isinstance(out.get("risk_blocks"), list):
        out["risk_blocks"] = out["risk_blocks"][:20]
    if isinstance(out.get("risk_block_items"), list):
        out["risk_block_items"] = out["risk_block_items"][:12]
    return out


def _five_question_digest(ops: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for qid, title, keys in _FIVE_Q:
        payload = {k: ops.get(k) for k in keys if k in ops}
        rows.append({"id": qid, "title": title, "fields": payload})
    return rows


def _load_paper(path: Optional[str] = None) -> Tuple[Optional[dict], Optional[str]]:
    p = path or PAPER_PATH
    if not os.path.isfile(p):
        return None, f"纸面账户不存在：{p}（请先初始化）"
    try:
        from core.paper import load_paper

        return load_paper(p), None
    except Exception as e:  # noqa: BLE001
        return None, f"读取纸面失败：{e}"


def _paper_service(path: Optional[str] = None):
    from services.paper_service import PaperService

    return PaperService(path=path or PAPER_PATH)


def _truncate_trades(trades: Any, n: int = 20) -> List[Dict[str, Any]]:
    if not isinstance(trades, list):
        return []
    out = []
    for t in trades[:n]:
        if not isinstance(t, dict):
            continue
        out.append(
            {
                k: t.get(k)
                for k in (
                    "stock_code",
                    "stock_name",
                    "side",
                    "action",
                    "shares",
                    "price",
                    "reason",
                    "blocked",
                )
                if k in t
            }
        )
    return out


def _preview_fingerprint(path: str, body: Dict[str, Any]) -> str:
    raw = json.dumps({"path": path, "body": body}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class AgentOpsService:
    """AO 轨应用服务。"""

    def __init__(self, paper_path: Optional[str] = None) -> None:
        self.paper_path = paper_path or PAPER_PATH

    def run(self, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        task = str(params.get("task") or "").strip().lower()
        if not task:
            return {
                "success": False,
                "error": "请指定 task",
                "available_tasks": list(AVAILABLE_TASKS),
                "note": "AO 轨：只读/预演为主；不改 ŷ；不自动 promote。",
            }
        if task not in AVAILABLE_TASKS:
            return {
                "success": False,
                "error": f"未知 task: {task}",
                "available_tasks": list(AVAILABLE_TASKS),
            }
        dispatch = {
            "ops_snapshot": self.ops_snapshot,
            "rebalance_preview": self.rebalance_preview,
            "ops_anomaly": self.ops_anomaly,
            "feedback_draft": self.feedback_draft,
            "prior_brief": self.prior_brief,
            "post_rebalance_review": self.post_rebalance_review,
            "event_flags": self.event_flags,
            "alpha_draft": self.alpha_draft,
            "view_bundle": self.view_bundle,
        }
        return dispatch[task](params)

    # ── AO1 ──────────────────────────────────────────────
    def ops_snapshot(self, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        paper, err = _load_paper(params.get("paper_path") or self.paper_path)
        if err:
            return {"success": False, "task": "ops_snapshot", "error": err}
        assert paper is not None
        ops = paper.get("last_ops_report")
        if not isinstance(ops, dict) or not ops:
            return {
                "success": False,
                "task": "ops_snapshot",
                "error": "尚无 last_ops_report：请先在交易执行做一次调仓预演或落账后再解释五问。",
                "hint": "交易执行 · 手动执行，或开盘自动调仓落账。",
            }
        compact = _compact_ops_report(ops)
        risk_tail = self._risk_block_tail(paper, limit=int(params.get("risk_limit") or 8))
        decisions = self._recent_decisions(limit=int(params.get("decision_limit") or 3))
        return {
            "success": True,
            "task": "ops_snapshot",
            "as_of": _utc_now(),
            "source": str(ops.get("source") or paper.get("last_ops_source") or "paper.last_ops_report"),
            "ops_report": compact,
            "five_questions": _five_question_digest(compact),
            "risk_block_tail": risk_tail,
            "recent_decisions": decisions,
            "disclaimer": "数字来自 last_ops_report；AI 不得改写。预演≠已成交。",
        }

    def _risk_block_tail(self, paper: dict, *, limit: int = 8) -> List[Dict[str, Any]]:
        try:
            from core.risk.block_outcome import list_risk_blocks

            blocks = list_risk_blocks(paper.get("operation_log") or [], limit=limit)
            if isinstance(blocks, list):
                return blocks[:limit]
            if isinstance(blocks, dict):
                items = blocks.get("items") or blocks.get("blocks") or []
                return list(items)[:limit] if isinstance(items, list) else []
        except Exception:  # noqa: BLE001
            pass
        # fallback：从 operation_log 抽 risk_block
        out = []
        for row in reversed(paper.get("operation_log") or []):
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or row.get("action") or "") != "risk_block":
                continue
            out.append(
                {
                    "ts": row.get("ts") or row.get("time"),
                    "detail": row.get("detail") or row.get("message"),
                    "meta": row.get("meta") or {},
                }
            )
            if len(out) >= limit:
                break
        return out

    def _recent_decisions(self, *, limit: int = 3) -> List[Dict[str, Any]]:
        try:
            from core.platform.decision_record import list_decisions

            data = list_decisions(limit=limit)
            items = data.get("items") or data.get("decisions") or data.get("records") or []
            if isinstance(data, list):
                items = data
            out = []
            for d in list(items)[:limit]:
                if not isinstance(d, dict):
                    continue
                out.append(
                    {
                        k: d.get(k)
                        for k in ("id", "ts", "source", "stock_code", "stance_label", "summary", "note")
                        if k in d
                    }
                )
            return out
        except Exception:  # noqa: BLE001
            return []

    # ── AO2 ──────────────────────────────────────────────
    def rebalance_preview(self, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        path = str(params.get("path") or "holding_cycle").strip().lower()
        if path not in PREVIEW_PATHS:
            return {
                "success": False,
                "task": "rebalance_preview",
                "error": f"未知 path: {path}",
                "allowed_paths": list(PREVIEW_PATHS),
            }
        # 硬拒写盘
        if params.get("dry_run") is False or params.get("execute") or params.get("confirm"):
            return {
                "success": False,
                "task": "rebalance_preview",
                "error": "本工具仅允许 dry_run/preview；禁止 execute/confirm/dry_run=false。",
            }
        try:
            if path == "holding_cycle":
                body = self._preview_holding_cycle(params)
            elif path == "watching_matrix":
                body = self._preview_watching_matrix(params)
            else:
                body = self._preview_sync_paper(params)
        except FileNotFoundError as e:
            return {"success": False, "task": "rebalance_preview", "path": path, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {
                "success": False,
                "task": "rebalance_preview",
                "path": path,
                "error": f"预演失败：{e}",
            }

        envelope = self._to_preview_envelope(path, body)
        envelope["preview_fingerprint"] = _preview_fingerprint(path, envelope)
        envelope["disclaimer"] = "此为预演，不等于已成交；请到交易执行或观察台确认。"
        return envelope

    def _preview_holding_cycle(self, params: dict) -> Dict[str, Any]:
        svc = _paper_service(params.get("paper_path") or self.paper_path)
        return svc.run(
            simulate_buy=bool(params.get("simulate_buy", False)),
            strategy=str(params.get("strategy") or "short_conservative"),
            dry_run=True,
        )

    def _preview_watching_matrix(self, params: dict) -> Dict[str, Any]:
        svc = _paper_service(params.get("paper_path") or self.paper_path)
        return svc.rebalance(
            dry_run=True,
            open_replay=bool(params.get("open_replay", False)),
            offline_only=bool(params.get("offline_only", False)),
        )

    def _preview_sync_paper(self, params: dict) -> Dict[str, Any]:
        from quant.services.quant_service import QuantService

        qs = QuantService()
        codes = params.get("codes") or params.get("stock_codes")
        if isinstance(codes, str):
            codes = [c.strip() for c in codes.split(",") if c.strip()]
        return qs.plan_watching_to_paper(
            codes=list(codes) if codes else None,
            shares=int(params["shares"]) if params.get("shares") is not None else None,
            amount_per_code=(
                float(params["amount_per_code"])
                if params.get("amount_per_code") is not None
                else None
            ),
            position_pct=(
                float(params["position_pct"])
                if params.get("position_pct") is not None
                else None
            ),
        )

    def _to_preview_envelope(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(body, dict):
            body = {"raw": str(body)[:500]}
        ops = body.get("ops_report") or body.get("last_ops_report")
        trades = (
            body.get("trades")
            or body.get("buy_trades")
            or body.get("new_trades")
            or body.get("planned_trades")
            or body.get("orders")
            or []
        )
        sells = body.get("sell_trades") or []
        risk_blocks = body.get("risk_blocks") or []
        if isinstance(ops, dict) and not risk_blocks:
            risk_blocks = ops.get("risk_blocks") or []
        checklist = []
        if body.get("buys_blocked") or (isinstance(ops, dict) and ops.get("buys_blocked")):
            checklist.append("存在 buys_blocked：加仓被风控拦截")
        if risk_blocks:
            checklist.append(f"risk_blocks×{len(risk_blocks) if isinstance(risk_blocks, list) else 1}")
        if body.get("preview") or body.get("dry_run") or path:
            checklist.append("dry_run/preview：未写盘")
        return {
            "success": bool(body.get("ok", True)) and "error" not in body,
            "task": "rebalance_preview",
            "path": path,
            "dry_run": True,
            "as_of": _utc_now(),
            "trades_summary": _truncate_trades(list(trades) + list(sells), 24),
            "buys_blocked": bool(
                body.get("buys_blocked")
                or (isinstance(ops, dict) and ops.get("buys_blocked"))
            ),
            "risk_blocks": list(risk_blocks)[:20] if isinstance(risk_blocks, list) else risk_blocks,
            "ops_report": _compact_ops_report(ops) if isinstance(ops, dict) else None,
            "checklist": checklist,
            "ok": body.get("ok"),
            "message": body.get("message") or body.get("error"),
            "preview_keys": sorted(k for k in body.keys() if k not in ("paper",)),
        }

    # ── AO3 ──────────────────────────────────────────────
    def ops_anomaly(self, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        paper, err = _load_paper(params.get("paper_path") or self.paper_path)
        ops = (paper or {}).get("last_ops_report") if paper else None
        ops = ops if isinstance(ops, dict) else {}

        health: Dict[str, Any] = {}
        try:
            from quant.services.quant_service import QuantService

            # health 经 quant skill 同路径
            from quant.skill.engine import QuantEngine

            health = QuantEngine().run({"task": "health"})
        except Exception as e:  # noqa: BLE001
            health = {"success": False, "error": str(e)}

        risk_summary: Dict[str, Any] = {}
        try:
            from core.north_star import summarize_risk_blocks

            if paper:
                risk_summary = summarize_risk_blocks(paper.get("operation_log") or [])
        except Exception as e:  # noqa: BLE001
            risk_summary = {"error": str(e)}

        timeline_tail: List[Dict[str, Any]] = []
        try:
            from web.audit_timeline import build_audit_timeline

            tl = build_audit_timeline(limit=int(params.get("timeline_limit") or 12))
            items = tl.get("items") or tl.get("events") or tl.get("timeline") or []
            if isinstance(tl, list):
                items = tl
            for row in list(items)[:12]:
                if isinstance(row, dict):
                    timeline_tail.append(
                        {
                            k: row.get(k)
                            for k in ("ts", "kind", "source", "summary", "title", "ref")
                            if k in row
                        }
                    )
        except Exception:  # noqa: BLE001
            pass

        alerts = list(ops.get("monitor_alerts") or [])[:10]
        dq = ops.get("data_quality") or {}
        return {
            "success": True,
            "task": "ops_anomaly",
            "as_of": _utc_now(),
            "monitor_alerts": alerts,
            "data_quality": dq,
            "risk_block_summary": risk_summary,
            "risk_block_tail": self._risk_block_tail(paper or {}, limit=8) if paper else [],
            "health": {
                k: health.get(k)
                for k in ("success", "error", "status", "summary", "alerts", "ok")
                if k in health
            }
            if isinstance(health, dict)
            else {},
            "timeline_tail": timeline_tail,
            "paper_loaded": paper is not None,
            "load_error": err,
            "disclaimer": "异常摘要只读；改配置须人审 promote。",
        }

    def prior_brief(self, params: Optional[dict] = None) -> Dict[str, Any]:
        """盘前市场先验结构化简报（只读；不 ingest、不写盘）。"""
        params = params or {}
        stock_code = params.get("stock_code") or params.get("code")
        sector = params.get("sector")
        code_s = str(stock_code).strip() if stock_code else None
        sector_s = str(sector).strip() if sector else None

        from core.market.context import summarize_market_context

        summary = summarize_market_context(
            stock_code=code_s or None,
            sector=sector_s or None,
        )
        fresh = summary.get("freshness") if isinstance(summary.get("freshness"), dict) else {}
        stale = bool(fresh.get("any_stale") or fresh.get("needs_ingest") or fresh.get("stale"))
        parts = fresh.get("parts") if isinstance(fresh.get("parts"), dict) else {}
        missing = False
        if parts:
            for _k, part in parts.items():
                if not isinstance(part, dict):
                    continue
                if part.get("as_of") is None and part.get("age_hours") is None:
                    missing = True
                    break
        if not fresh and not summary.get("macro"):
            stale = True
            missing = True

        regime_summary: Dict[str, Any] = {}
        try:
            from core.signal.config import load_signal_config
            from core.signal.live_features import fetch_live_index_bars
            from core.signal.regime import assess_regime
            from core.pro_core import regime_position_scale

            sig_cfg = load_signal_config() or {}
            idx_pack = fetch_live_index_bars(limit=75, use_cache=True)
            regime_info = assess_regime(
                idx_pack.get("bars") or [],
                sig_cfg.get("regime"),
                macro=None,
            )
            scale_info = regime_position_scale(regime=regime_info)
            regime_summary = {
                "regime": regime_info.get("regime"),
                "index_return_pct": regime_info.get("index_return_pct"),
                "volatility_pct": regime_info.get("volatility_pct"),
                "position_scale": scale_info.get("scale"),
                "apply_position_scale": (sig_cfg.get("regime") or {}).get(
                    "apply_position_scale"
                )
                is not False,
                "reason": regime_info.get("reason"),
            }
        except Exception:  # noqa: BLE001 — 简报降级，不阻塞
            regime_summary = {}

        flags = summary.get("prior_flags") if isinstance(summary.get("prior_flags"), dict) else {}
        prior_active = bool(summary.get("prior_active"))
        warnings = list(summary.get("prior_warnings") or [])[:8]

        exposure_note = "未指定个股：请对照 Prior Desk「事件与暴露」与观察池/持仓交集。"
        if code_s:
            exposure_note = (
                f"已按 {code_s}"
                + (f" / {sector_s}" if sector_s else "")
                + " 裁剪 prior warnings；是否命中监管/IPO 见 prior_flags。"
            )

        checklist = [
            {
                "id": "freshness",
                "ok": not stale and not missing,
                "label": "盘前 ingest 新鲜度",
                "detail": (
                    "上下文过期或缺失 · 请在 Prior Desk 点「刷新 ingest」或跑 pre_market_ingest"
                    if (stale or missing)
                    else "freshness 可用"
                ),
            },
            {
                "id": "prior_active",
                "ok": True,
                "label": "激活的 M 层 prior",
                "detail": (
                    "；".join(warnings)
                    if prior_active and warnings
                    else ("prior 未激活" if not prior_active else "prior 激活但无 warnings 文本")
                ),
                "flags": flags,
            },
            {
                "id": "exposure",
                "ok": True,
                "label": "对观察池 / 持仓暴露",
                "detail": exposure_note,
            },
            {
                "id": "rebalance_scale",
                "ok": True,
                "label": "今日调仓是否会被缩放",
                "detail": (
                    "prior_active=true → 纸面买/持仓可能按 scale_buy_pct 缩放；ŷ 不变"
                    if prior_active
                    else "prior 未激活 → 本层不额外缩放；仍可能受 Regime 仓位上限影响"
                ),
            },
        ]

        return {
            "success": True,
            "task": "prior_brief",
            "as_of": _utc_now(),
            "stock_code": code_s,
            "sector": sector_s,
            "freshness": fresh,
            "macro": summary.get("macro") or {},
            "sentiment": summary.get("sentiment") or {},
            "regulatory": summary.get("regulatory") or {},
            "ipo": summary.get("ipo") or {},
            "prior_active": prior_active,
            "prior_flags": flags,
            "prior_warnings": warnings,
            "regime_summary": regime_summary,
            "checklist": checklist,
            "impact_note": "不进 ŷ；仅纸面买/持仓缩放",
            "disclaimer": "只读简报；数字来自 market_context；AI 不得改写；不写 signal_config。",
            "hint": (
                "若 freshness 过期：Prior Desk「刷新 ingest」或 schedule pre_market_ingest。"
                if (stale or missing)
                else None
            ),
        }

    def feedback_draft(self, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        scope = str(params.get("scope") or "strategy").strip().lower()
        if scope in ("market_prior", "prior", "m_prior"):
            return self._feedback_draft_market_prior(params)

        paper, _err = _load_paper(params.get("paper_path") or self.paper_path)
        ops = (paper or {}).get("last_ops_report") if paper else None
        ops = ops if isinstance(ops, dict) else {}
        paper_metrics = params.get("paper_metrics") or {}
        if isinstance(ops.get("monitor_metrics"), dict):
            paper_metrics = {**ops["monitor_metrics"], **paper_metrics}
        if isinstance(ops.get("north_star"), dict):
            ns = ops["north_star"]
            for k in ("rolling_sharpe", "calmar", "corr", "tracking_error_pct"):
                if k in ns and k not in paper_metrics:
                    paper_metrics[k] = ns[k]
        alerts = params.get("monitor_alerts") or ops.get("monitor_alerts") or []

        from core.feedback_suggest import suggest_config_feedback

        draft = suggest_config_feedback(
            paper_metrics=paper_metrics or None,
            backtest_metrics=params.get("backtest_metrics"),
            monitor_alerts=list(alerts) if alerts else None,
        )
        baseline = {
            "strategy_id": ops.get("strategy_id"),
            "strategy_version": ops.get("strategy_version"),
        }
        suggested_checks = [
            "quant(task=portfolio_backtest) 对照净值/回撤",
            "quant(task=health) 确认监控告警",
            "ops(task=post_rebalance_review) 调仓一致性",
            "人审后 POST /api/strategy/promote（Agent 不调用）",
        ]
        return {
            "success": True,
            "task": "feedback_draft",
            "scope": "strategy",
            "as_of": _utc_now(),
            "baseline_ref": baseline,
            "draft": draft,
            "suggested_checks": suggested_checks,
            "applied": False,
            "disclaimer": "仅草稿，未写盘；未完成对照不得宣称策略变好。",
        }

    def _feedback_draft_market_prior(self, params: Optional[dict] = None) -> Dict[str, Any]:
        """仅建议 M 层 prior 键；不写盘、不改 weights/scoring。"""
        params = params or {}
        from core.market.context import summarize_market_context
        from core.signal.market_prior_config import read_market_prior_public

        summary = summarize_market_context()
        cfg_pub = read_market_prior_public()
        flags = summary.get("prior_flags") if isinstance(summary.get("prior_flags"), dict) else {}
        prior_active = bool(summary.get("prior_active"))
        warnings = list(summary.get("prior_warnings") or [])[:8]

        paper, _err = _load_paper(params.get("paper_path") or self.paper_path)
        trim_count = 0
        if paper:
            for t in list(paper.get("trades") or [])[-80:]:
                if not isinstance(t, dict):
                    continue
                note = str(t.get("reason") or t.get("note") or "")
                if t.get("market_prior") or "market_prior" in note or "市场先验" in note:
                    trim_count += 1

        suggestions: List[Dict[str, Any]] = []
        cm = cfg_pub.get("cross_market") or {}
        msp = cfg_pub.get("market_sentiment_prior") or {}
        reg = cfg_pub.get("regulatory_prior") or {}
        ipo = cfg_pub.get("ipo_drain_prior") or {}
        policy = cfg_pub.get("market_prior_policy") or {}

        if flags.get("cross_market"):
            cur = float(cm.get("scale_buy_pct") or 0.5)
            suggestions.append(
                {
                    "key": "cross_market.scale_buy_pct",
                    "current": cur,
                    "suggested": round(max(0.25, min(0.85, cur * 0.9)), 3),
                    "reason": "跨市场 prior 已激活；若缩仓过猛可上调，过松可下调（本草稿偏保守 ×0.9）",
                }
            )
        if flags.get("market_sentiment"):
            cur = float(msp.get("scale_buy_pct") or 0.55)
            suggestions.append(
                {
                    "key": "market_sentiment_prior.scale_buy_pct",
                    "current": cur,
                    "suggested": round(max(0.25, min(0.85, cur * 0.9)), 3),
                    "reason": "情绪 prior 激活；建议复核 scale_buy_pct",
                }
            )
        if flags.get("regulatory"):
            suggestions.append(
                {
                    "key": "regulatory_prior.mode",
                    "current": reg.get("mode"),
                    "suggested": reg.get("mode") or "scale",
                    "reason": "监管 prior 激活；确认 mode 与 scale_buy_pct 是否符合当日暴露",
                }
            )
        if flags.get("ipo_drain"):
            cur = float(ipo.get("scale_buy_pct") or 0.45)
            suggestions.append(
                {
                    "key": "ipo_drain_prior.scale_buy_pct",
                    "current": cur,
                    "suggested": round(max(0.2, min(0.8, cur * 0.9)), 3),
                    "reason": "IPO 虹吸 prior 激活；建议复核缩放",
                }
            )
        if trim_count >= 3 and not suggestions:
            cur = float(cm.get("scale_buy_pct") or 0.5)
            suggestions.append(
                {
                    "key": "cross_market.scale_buy_pct",
                    "current": cur,
                    "suggested": round(min(0.9, cur + 0.05), 3),
                    "reason": f"近端纸面 trades 含 market_prior 标记约 {trim_count} 笔；若过抑可略上调缩放",
                }
            )
        if not suggestions:
            suggestions.append(
                {
                    "key": "market_prior_policy.merge_mode",
                    "current": policy.get("merge_mode") or "min_scale",
                    "suggested": policy.get("merge_mode") or "min_scale",
                    "reason": "当前无强激活 prior；保持 merge_mode，无需改 weights/scoring",
                }
            )

        draft = {
            "scope": "market_prior",
            "prior_active": prior_active,
            "prior_flags": flags,
            "prior_warnings": warnings,
            "market_prior_trim_trades": trim_count,
            "current_config": cfg_pub,
            "suggestions": suggestions,
            "forbidden_keys": ["weights", "scoring", "yhat", "stance"],
            "write_path": "人审后 POST /api/signal/config/market-prior（Agent 不调用）",
        }
        suggested_checks = [
            "ops(task=prior_brief) 确认 freshness 与激活 flags",
            "ops(task=rebalance_preview) 纸面 dry 预演对照缩放",
            "对照 prior 激活日 vs 非激活日缩仓比例（纸面 trades.market_prior）",
            "人审后 POST /api/signal/config/market-prior（勿在 /market-prior 页写盘；Agent 不调用）",
        ]
        return {
            "success": True,
            "task": "feedback_draft",
            "scope": "market_prior",
            "as_of": _utc_now(),
            "baseline_ref": {
                "merge_mode": policy.get("merge_mode"),
                "cross_market_mode": cm.get("mode"),
            },
            "draft": draft,
            "suggested_checks": suggested_checks,
            "applied": False,
            "disclaimer": (
                "仅 M 层 prior 草稿，未写盘；不得改 ŷ/weights；"
                "未完成对照不得宣称策略变好。"
            ),
        }

    # ── AO3.5 ────────────────────────────────────────────
    def post_rebalance_review(self, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        snap = self.ops_snapshot(params)
        if not snap.get("success"):
            return {
                "success": False,
                "task": "post_rebalance_review",
                "error": snap.get("error") or "无 ops_report 可对照",
            }
        ops = snap.get("ops_report") or {}
        consistent = []
        concerns = []
        if ops.get("strategy_id"):
            consistent.append(f"策略 {ops.get('strategy_id')}@{ops.get('strategy_version')}")
        if ops.get("buys_blocked"):
            concerns.append("buys_blocked=true：加仓被拦，核对 risk_blocks")
        rb = ops.get("risk_blocks") or []
        if isinstance(rb, list) and rb:
            concerns.append(f"risk_blocks 条数={len(rb)}")
        elif not ops.get("buys_blocked"):
            consistent.append("无 buys_blocked / 空 risk_blocks（或未记录）")
        dq = ops.get("data_quality") or {}
        fc = int(ops.get("fallback_count") or dq.get("fallback_count") or 0)
        if fc > 0:
            concerns.append(f"data_quality.fallback_count={fc}")
        else:
            consistent.append("fallback_count=0 或未计")
        mm = ops.get("monitor_metrics") or {}
        if mm.get("rolling_ic") is not None:
            consistent.append(f"rolling_ic={mm.get('rolling_ic')}")
        alerts = ops.get("monitor_alerts") or []
        if alerts:
            concerns.append(f"monitor_alerts×{len(alerts)}")

        return {
            "success": True,
            "task": "post_rebalance_review",
            "as_of": _utc_now(),
            "ops_ref": {
                "strategy_id": ops.get("strategy_id"),
                "strategy_version": ops.get("strategy_version"),
                "buys_blocked": ops.get("buys_blocked"),
                "fallback_count": ops.get("fallback_count"),
            },
            "consistent": consistent,
            "concerns": concerns,
            "do_not_auto_patch": [
                "本工具不产生 signal_config patch",
                "若需改参：ops(task=feedback_draft) → 人审 promote",
            ],
            "disclaimer": "只读对照；不改仓、不 promote。",
        }

    # ── AO5 ──────────────────────────────────────────────
    def event_flags(self, params: Optional[dict] = None) -> Dict[str, Any]:
        """从持仓/观察池代码拉新闻标题，启发式生成 event_flags（不改 ŷ）。"""
        params = params or {}
        codes = params.get("codes") or params.get("stock_codes") or []
        if isinstance(codes, str):
            codes = [c.strip() for c in codes.split(",") if c.strip()]
        if not codes:
            paper, _ = _load_paper(params.get("paper_path") or self.paper_path)
            holdings = (paper or {}).get("holdings") or []
            for h in holdings:
                if isinstance(h, dict) and h.get("stock_code"):
                    codes.append(str(h["stock_code"]))
            codes = codes[:12]

        flags: List[Dict[str, Any]] = []
        keyword_sev = (
            (("停牌", "立案", "退市", "爆雷", "造假"), "high", "risk_halt"),
            (("减持", "增发", "亏损", "下滑", "警示"), "medium", "caution"),
            (("中标", "回购", "超预期", "高增"), "low", "positive"),
        )
        try:
            from skills.news.handler import NewsHandler

            nh = NewsHandler()
            for code in codes[:8]:
                raw = nh.execute({"name": "news", "parameters": {"stock_code": code, "limit": 5}})
                data = json.loads(raw) if isinstance(raw, str) else raw
                titles = []
                if isinstance(data, dict):
                    for item in data.get("news") or data.get("items") or data.get("titles") or []:
                        if isinstance(item, dict):
                            titles.append(str(item.get("title") or item.get("summary") or ""))
                        else:
                            titles.append(str(item))
                blob = " ".join(titles)
                for keys, sev, etype in keyword_sev:
                    if any(k in blob for k in keys):
                        hit = next(k for k in keys if k in blob)
                        flags.append(
                            {
                                "symbol": code,
                                "event_type": etype,
                                "severity": sev,
                                "expires": None,
                                "evidence": hit,
                                "title_sample": titles[:2],
                            }
                        )
                        break
        except Exception as e:  # noqa: BLE001
            return {
                "success": True,
                "task": "event_flags",
                "flags": [],
                "applied_to_rules": False,
                "warning": f"新闻拉取降级：{e}",
                "note": "规则侧消费 flag 需 AO5 接线 risk_budget_skips；当前仅产出结构化列表。",
            }

        return {
            "success": True,
            "task": "event_flags",
            "as_of": _utc_now(),
            "flags": flags,
            "codes_scanned": list(codes)[:8],
            "applied_to_rules": False,
            "note": "flag 供规则/人审；不改 ŷ。applied_to_rules=false 表示尚未写入跳过闸。",
            "disclaimer": "启发式标题标签，须人工确认。",
        }

    # ── AO6 ──────────────────────────────────────────────
    def alpha_draft(self, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        idea = str(params.get("idea") or params.get("hypothesis") or params.get("text") or "").strip()
        if not idea:
            return {
                "success": False,
                "task": "alpha_draft",
                "error": "请提供 idea/hypothesis 描述因子假设",
            }
        direction = "unknown"
        if any(k in idea for k in ("动量", "涨幅", "强者", "momentum")):
            direction = "higher_better"
        elif any(k in idea for k in ("反转", "超卖", "低波", "质量")):
            direction = "context_dependent"
        draft = {
            "title": idea[:80],
            "hypothesis": idea[:500],
            "expected_direction": direction,
            "suggested_fields": re.findall(r"[a-z_]{3,}", idea.lower())[:12],
            "test_window_hint": "建议 lookback≥120 交易日 + holdout OOS",
            "fail_conditions": [
                "holdout IC 不显著或变号",
                "换手/成本后无增量",
                "与现有主因子相关过高且无正交收益",
            ],
        }
        return {
            "success": True,
            "task": "alpha_draft",
            "as_of": _utc_now(),
            "draft": draft,
            "next_steps": [
                "quant(task=factor_ols 或 factor_corr) 检验",
                "quant(task=config_diff) 预览（不写盘）",
                "OOS 达标后人工 promote",
            ],
            "applied": False,
            "promote_allowed": False,
            "disclaimer": "研究草稿；未过 OOS 不得建议上线；Agent 不调用 promote。",
        }

    # ── AO7 ──────────────────────────────────────────────
    def view_bundle(self, params: Optional[dict] = None) -> Dict[str, Any]:
        params = params or {}
        raw_views = params.get("views") or []
        if isinstance(raw_views, dict):
            raw_views = [raw_views]
        cleaned = []
        warnings = []
        for i, v in enumerate(raw_views):
            if not isinstance(v, dict):
                warnings.append(f"views[{i}] 非对象，已丢弃")
                continue
            sym = str(v.get("symbol") or v.get("stock_code") or "").strip()
            if not sym:
                warnings.append(f"views[{i}] 缺 symbol，已丢弃")
                continue
            conf = v.get("confidence", 0.5)
            try:
                conf_f = max(0.0, min(1.0, float(conf)))
            except (TypeError, ValueError):
                conf_f = 0.5
                warnings.append(f"{sym}: confidence 非法，置 0.5")
            cleaned.append(
                {
                    "symbol": sym,
                    "direction": str(v.get("direction") or v.get("view") or "neutral"),
                    "mu_tilt": v.get("mu_tilt"),
                    "confidence": conf_f,
                    "evidence_refs": list(v.get("evidence_refs") or v.get("evidence") or [])[:5],
                }
            )
        enabled = os.environ.get("AGENT_VIEWS_FUSION", "0").strip() in ("1", "true", "TRUE")
        bundle = {
            "as_of": _utc_now(),
            "views": cleaned,
            "views_hash": hashlib.sha256(
                json.dumps(cleaned, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()[:12]
            if cleaned
            else None,
        }
        return {
            "success": True,
            "task": "view_bundle",
            "bundle": bundle,
            "warnings": warnings,
            "fusion_enabled": enabled,
            "applied": False,
            "note": "默认不融合进优化器（AGENT_VIEWS_FUSION=0）。开启后由组合层消费，Agent 不直接返回目标权重。",
            "disclaimer": "views≠成交指令；权重须确定性优化器计算。",
        }


def truncate_tool_json_for_llm(result: str, *, max_chars: int = 6000) -> str:
    """压缩写入 messages 的 tool JSON；artifact 仍可用原文。"""
    if not isinstance(result, str) or len(result) <= max_chars:
        return result
    try:
        data = json.loads(result)
    except Exception:  # noqa: BLE001
        return result[: max_chars - 20] + "…(truncated)"
    if not isinstance(data, dict):
        return result[: max_chars - 20] + "…(truncated)"

    def _shrink(obj: Any, depth: int = 0) -> Any:
        if depth > 4:
            return "…"
        if isinstance(obj, list):
            return [_shrink(x, depth + 1) for x in obj[:12]]
        if isinstance(obj, dict):
            out = {}
            for i, (k, v) in enumerate(obj.items()):
                if i >= 40:
                    out["…"] = f"+{len(obj) - 40} keys"
                    break
                out[k] = _shrink(v, depth + 1)
            return out
        if isinstance(obj, str) and len(obj) > 400:
            return obj[:400] + "…"
        return obj

    slim = _shrink(data)
    slim["_truncated_for_llm"] = True
    text = json.dumps(slim, ensure_ascii=False)
    if len(text) > max_chars:
        return text[: max_chars - 20] + "…(truncated)"
    return text
