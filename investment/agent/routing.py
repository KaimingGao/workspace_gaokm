"""用户意图路由：关键词检测与 Agent 提示注入（单一事实源）。"""

from __future__ import annotations

from typing import Any, Dict

from agent.prompts import (
    ANALYSIS_HINT,
    BUY_QUESTION_HINT,
    DEEP_ANALYSIS_KEYWORDS,
    POSITION_BASE_HINT,
    POSITION_HINT,
    SCORE_STANCE_HINT,
    MODEL_POLICY_HINT,
    SHORT_HORIZON_HINT,
    QUANT_HINT,
)

BUY_QUESTION_KEYS = (
    "能否买入",
    "能不能买",
    "可不可以买",
    "是否可以买入",
    "该不该买",
    "值得买",
    "可以买",
    "要不要买",
    "适合买",
)

ANALYSIS_KEYS = DEEP_ANALYSIS_KEYWORDS


SHORT_HORIZON_KEYS = (
    "短线",
    "观察池",
    "1-3",
    "1～3",
    "三天",
    "未来三天",
)

SCORE_QUESTION_KEYS = (
    "score",
    "评分",
    "打分",
    "短线分",
    "动能分",
)

MODEL_QUESTION_KEYS = (
    "线性回归",
    "机器学习",
    "神经网络",
    "深度学习",
    "xgboost",
    "gbdt",
    "拟合模型",
    "训练模型",
    "预估模型",
    "预测模型",
)

QUANT_KEYS = (
    "量化",
    "quant",
    "观察池组合",
    "组合回测",
    "topk回测",
    "top-k回测",
    "top k回测",
    "横截面",
    "因子 ic",
    "因子IC",
    "权重建议",
    "阈值校准",
    "量化报告",
    "quant_daily",
    "historically",
    "signal_config",
    "diff",
    "preset",
    "定时",
    "cron",
    "持仓联动",
    "中性化",
    "中性化对照",
    "包结构",
    "模块树",
)

POSITION_KEYS = (
    "持仓",
    "仓位",
    "减仓",
    "止损",
    "止盈",
    "加仓",
    "增仓",
    "我的股",
    "我的股票",
    "我的票",
    "我的组合",
    "portfolio",
    "浮亏",
    "浮盈",
    "套牢",
    "回本",
    "成本价",
    "持仓成本",
)

POSITION_STANCE_KEYS = (
    "加仓",
    "增仓",
    "补仓",
    "补一点",
    "补点",
    "再买",
    "继续买",
    "继续加",
    "还能加",
    "还能不能加",
    "追加",
    "再入",
    "再进",
    "入仓",
    "上车",
    "抄底",
    "摊平",
    "摊薄",
    "买进",
    "要不要买",
    "能不能买",
    "可否买入",
    "能否买入",
    "是否可以买入",
    "该不该买",
    "适合买",
    "值得买",
    "可以买",
    "买入",
    "加吗",
    "加不加",
    "要不要加",
    "减还是加",
    "加还是减",
)

DISCLAIMER_KEYWORDS = (
    "股",
    "行情",
    "涨",
    "跌",
    "PE",
    "市盈",
    "筛选",
    "对比",
    "解读",
    "投资",
    "仓位",
    "买入",
    "卖出",
    "估值",
    "观察池",
    "持仓",
    "短线",
    "减仓",
    "止损",
    "止盈",
    "K线",
    "k线",
    "阴线",
    "阳线",
    "基本面",
    "同行",
    "超额",
    "ROE",
    "新闻",
    "资讯",
    "公告",
)


def is_buy_question(text: str) -> bool:
    t = (text or "").strip()
    return any(k in t for k in BUY_QUESTION_KEYS)


def is_analysis_question(text: str) -> bool:
    t = (text or "").strip()
    return any(k in t for k in ANALYSIS_KEYS)


def is_short_horizon_question(text: str) -> bool:
    t = (text or "").strip()
    return any(k in t for k in SHORT_HORIZON_KEYS)


def mentions_score(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(k in t for k in SCORE_QUESTION_KEYS)


def mentions_model_policy(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(k in t for k in MODEL_QUESTION_KEYS)


def is_quant_question(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(k.lower() in t for k in QUANT_KEYS)


def infer_quant_task(text: str) -> str:
    """量化问题默认 task 推断（Agent 未显式传 task 时补全）。"""
    t = (text or "").strip()
    if any(k in t for k in ("preset", "定时", "cron", "daily 任务", "daily任务")):
        return "daily_presets"
    if any(k in t for k in ("持仓对照", "持仓联动", "持仓和量化", "持仓 量化")):
        return "portfolio_bridge"
    if any(k in t for k in ("包结构", "模块树", "quant 包", "quant/")):
        return "package_info"
    if any(k in t for k in ("做T", "做 t", "底仓做T", "日内回转", "t0_backtest", "T+0模拟")):
        return "t0_backtest"
    if "解读" in t and any(k in t for k in ("量化", "报告", "日报")):
        return "interpret"
    if any(k in t for k in ("日报", "quant_daily", "量化报告")) and any(
        k in t for k in ("摘要", "专节", "字段", "包含", "写了什么")
    ):
        return "daily_summary"
    if any(k in t for k in ("中性化对照", "中性化 vs", "截面中性化", "绝对分对照", "neutral compare")) or (
        "中性化" in t and ("绝对分" in t or "对照" in t or "回测差" in t)
    ):
        return "portfolio_neutral_compare"
    if any(k in t for k in ("横截面", "排序", "Top N", "top")):
        return "cross_section"
    if any(k in t.lower() for k in ("factor_ols", "截面ols", "截面 ols", "面板 ols")) or (
        "ols" in t.lower()
        and any(k in t for k in ("实验", "研究", "拟合", "估计", "面板"))
    ) or any(k in t for k in ("对比 config 权重", "对比权重", "config 权重")):
        return "factor_ols"
    if any(k in t for k in ("权重", "IC", "因子")):
        return "weight_suggest"
    if any(k in t for k in ("阈值", "stance", "校准")):
        return "threshold_suggest"
    if any(k in t for k in ("diff", "合并配置", "signal_config", "配置预览", "改配置")):
        return "config_diff"
    if any(k in t for k in ("日报", "摘要", "量化报告")):
        return "daily_summary"
    if any(k in t for k in ("健康", "状态", "运维", "是否正常")):
        return "health"
    if any(k in t for k in ("组合", "观察池", "historically", "历史表现")):
        return "portfolio_backtest"
    return "daily_summary"


def is_position_question(text: str) -> bool:
    t = (text or "").strip()
    return any(k in t for k in POSITION_KEYS)


def wants_position_stance(text: str) -> bool:
    """持仓问题且涉及加仓/买入倾向 → position 应带 include_stance。"""
    t = (text or "").strip()
    if not is_position_question(t):
        return False
    return is_buy_question(t) or any(k in t for k in POSITION_STANCE_KEYS)


def build_user_hints(user_input: str) -> str:
    """按意图追加系统侧 hint，减少 prompts 与 agent 行为漂移。"""
    hints = []
    if is_position_question(user_input):
        hints.append(POSITION_BASE_HINT)
    if is_buy_question(user_input) and not is_position_question(user_input):
        hints.append(BUY_QUESTION_HINT)
    elif wants_position_stance(user_input):
        hints.append(POSITION_HINT)
    elif is_buy_question(user_input):
        hints.append(BUY_QUESTION_HINT)
    if is_analysis_question(user_input):
        hints.append(ANALYSIS_HINT)
    if is_short_horizon_question(user_input):
        hints.append(SHORT_HORIZON_HINT)
    if mentions_score(user_input) or is_short_horizon_question(user_input):
        hints.append(SCORE_STANCE_HINT)
    if mentions_model_policy(user_input):
        hints.append(MODEL_POLICY_HINT)
    if is_quant_question(user_input):
        hints.append(QUANT_HINT)
    if not hints:
        return user_input.strip()
    block = "\n\n".join(hints)
    return f"{user_input.strip()}\n\n{block}"


def prepare_tool_params(tool_name: str, params: Dict[str, Any], user_input: str) -> Dict[str, Any]:
    """Agent 侧参数补全（不依赖 LLM 是否记得传 include_stance）。"""
    out = dict(params or {})
    if tool_name == "position" and wants_position_stance(user_input):
        out.setdefault("include_stance", True)
    if tool_name == "quant" and is_quant_question(user_input):
        out.setdefault("task", infer_quant_task(user_input))
    return out


def enrich_tool_result(tool_name: str, result: str) -> str:
    """工具结果后处理：强化 advise stance 约束。"""
    if tool_name != "advise":
        return result
    try:
        import json

        from core.stance import format_stance_block

        data = json.loads(result)
        if data.get("success") and data.get("stance_label"):
            block = format_stance_block(data)
            return result.rstrip() + "\n\n" + block
    except (json.JSONDecodeError, TypeError, KeyError):
        pass
    return result


def needs_disclaimer(user_input: str, content: str) -> bool:
    return any(
        k.lower() in user_input.lower() or k in content for k in DISCLAIMER_KEYWORDS
    )
