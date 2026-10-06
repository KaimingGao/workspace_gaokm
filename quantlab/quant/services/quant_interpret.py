"""量化报告 LLM 解读（P13.2）。"""


import logging

logger = logging.getLogger(__name__)
import json
from typing import Any, Dict, List

QUANT_INTERPRET_SYSTEM = """你是 QuantLab 量化研究台的解读助手。
根据用户提供的量化日报 JSON 摘要，用中文输出 3～6 条要点：
1) 因子 IC / 权重建议遗留诊断（若有；权重建议不驱动选股）
2) **横截面 ŷ（predicted_score）排序**（若有 cross_section：须写 Top 标的与 ŷ 分布；ŷ 为模型预测收益百分点，不等于买入）
3) 历史回测摘要（若有）
4) stance 阈值建议（若有；按 ŷ% 门槛）
5) **因子 OLS 实验**（若有 factor_ols：R²/样本与 config 对照；研究用，不写 config）
6) OOS / 分组 live（若有：heuristic 基线 vs ŷ；过门≠自动 promote）
7) 风险与局限（样本、OOS、非 point-in-time、非实盘）

要求：只解读给定数据，不保证收益，不推荐具体买卖；不升级/降级 stance_label 结论。
强调：选股真源是 predicted_score（ŷ），不是人工 weights。"""


def compact_quant_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """压缩报告供 LLM 消费，避免超长 context。"""
    out: Dict[str, Any] = {"success": report.get("success")}
    if report.get("factor_ic"):
        ic = report["factor_ic"]
        out["factor_ic"] = {
            "stock_code": ic.get("stock_code"),
            "sample_count": ic.get("sample_count"),
            "factors": (ic.get("factors") or [])[:6],
        }
    if report.get("weight_suggest") and report["weight_suggest"].get("success"):
        ws = report["weight_suggest"]
        out["weight_suggest"] = {
            "current_weights": ws.get("current_weights"),
            "suggested_weights": ws.get("suggested_weights"),
            "rationale": (ws.get("rationale") or [])[:4],
            "deprecated_for_scoring": True,
            "note": ws.get("note")
            or "遗留诊断；选股真源为 predicted_score（ŷ）",
        }
    fo = report.get("factor_ols") or {}
    if fo.get("success"):
        from quant.services.quant_report_export import summarize_factor_ols

        sm = summarize_factor_ols(fo)
        if sm:
            out["factor_ols"] = {
                "summary_line": sm.get("summary_line"),
                "r_squared": sm.get("r_squared"),
                "sample_count": sm.get("sample_count"),
                "top_deltas": (sm.get("top_deltas") or [])[:3],
                "excluded_features": sm.get("excluded_features"),
            }
    if report.get("threshold_suggest") and report["threshold_suggest"].get("success"):
        ts = report["threshold_suggest"]
        out["threshold_suggest"] = {
            "current_thresholds": ts.get("current_thresholds"),
            "suggested_thresholds": ts.get("suggested_thresholds"),
            "rationale": (ts.get("rationale") or [])[:4],
        }
    if report.get("portfolio_backtest_summary"):
        out["portfolio_backtest_summary"] = report["portfolio_backtest_summary"]
    cs = report.get("cross_section") or {}
    if cs.get("success"):
        from quant.services.quant_report_export import summarize_cross_section_scores

        sm = summarize_cross_section_scores(cs)
        if sm:
            out["cross_section"] = sm
    if report.get("scoring"):
        out["scoring"] = report.get("scoring")
    cl = report.get("cluster_live") or {}
    if cl and cl.get("mode") in ("shadow", "active"):
        out["cluster_live"] = {
            "mode": cl.get("mode"),
            "version": cl.get("version"),
            "oos_summary": cl.get("oos_summary"),
            "note": cl.get("note"),
        }
    return out


def build_rule_based_interpret(report: Dict[str, Any]) -> Dict[str, Any]:
    """离线规则解读（P63 golden / 无 LLM）。"""
    compact = compact_quant_report(report)
    bullets: List[str] = []

    ic = compact.get("factor_ic") or {}
    factors = ic.get("factors") or []
    if factors:
        top = factors[0]
        bullets.append(
            f"因子 IC：{top.get('label') or top.get('factor')} {top.get('ic')} "
            f"(n={ic.get('sample_count') or top.get('sample_count') or '—'})"
        )

    from quant.services.quant_report_export import summarize_factor_ols

    ols_sm = summarize_factor_ols(report.get("factor_ols") or {})
    if ols_sm:
        bullets.append(ols_sm["summary_line"])
        top_delta = (ols_sm.get("top_deltas") or [{}])[0]
        if top_delta.get("factor"):
            bullets.append(
                f"OLS 最大偏差：{top_delta['factor']} OLS {top_delta.get('ols')} "
                f"vs config {top_delta.get('config')}"
            )

    from quant.services.quant_report_export import (
        _fmt_yhat,
        summarize_cross_section_scores,
    )

    cs_sm = summarize_cross_section_scores(report.get("cross_section") or {})
    if cs_sm:
        bullets.append(cs_sm["summary_line"])
        top_row = (cs_sm.get("top") or [{}])[0]
        if top_row.get("score") is not None:
            bullets.append(
                f"横截面 Top1：{top_row.get('stock_name') or top_row.get('stock_code')} "
                f"ŷ {_fmt_yhat(top_row.get('score'))}（predicted_score，不等于买入指令）"
            )

    ps = compact.get("portfolio_backtest_summary") or {}
    if ps.get("success"):
        bullets.append(
            f"历史回测：累计 {ps.get('total_return_pct')}% · "
            f"胜率 {ps.get('win_rate_pct')}% · 交易 {ps.get('trade_count')}"
        )

    if not bullets:
        bullets.append("量化日报数据不足，无法生成解读要点")

    bullets.append("研究结论非实盘建议，样本与基本面为快照，非 point-in-time")

    return {
        "success": True,
        "interpretation": "\n".join(f"{i + 1}. {line}" for i, line in enumerate(bullets)),
        "source": "rule_based",
        "note": "以上为 AI 对量化研究数据的解读，市场有风险，不保证收益，不代客下单。",
    }


def interpret_quant_report(
    report: Dict[str, Any],
    *,
    llm_client=None,
) -> Dict[str, Any]:
    """调用 LLM 解读量化日报（无工具调用）。"""
    from agent.llm_client import LLMClient, parse_usage

    llm = llm_client or LLMClient()
    if not llm.api_key:
        return {"success": False, "error": "未配置 DASHSCOPE_API_KEY"}
    if not llm.is_available():
        return {"success": False, "error": llm.get_last_error() or "LLM 不可用"}

    payload = compact_quant_report(report)
    messages = [
        {"role": "system", "content": QUANT_INTERPRET_SYSTEM},
        {
            "role": "user",
            "content": "请解读以下量化日报摘要（JSON）：\n"
            + json.dumps(payload, ensure_ascii=False, indent=2),
        },
    ]
    try:
        response = llm.chat(messages)
        text = llm.get_response_content(response) or ""
        usage = parse_usage(response)
    except Exception as e:
        logger.exception('unexpected error in interpret_quant_report')
        return {"success": False, "error": str(e)}

    if not text.strip():
        return {"success": False, "error": "LLM 返回为空"}

    out = {
        "success": True,
        "interpretation": text.strip(),
        "usage": usage,
        "source": "llm",
        "note": "以上为 AI 对量化研究数据的解读，市场有风险，不保证收益，不代客下单。",
    }
    return out
