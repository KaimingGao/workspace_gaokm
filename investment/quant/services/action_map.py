"""产品动作映射（与 Web 量化研究台对齐）。

主线：基于信号开发决策策略，用历史/前瞻验证有效性；对比（按需）看两条验证是否一致。
UI：量化 Tab = 验证台（策略摘要 + 双栏验证 + 按需对比）；执行在纸面 Tab。
API URL 不变（P94）；本模块只提供归属说明与机器可读映射。
共享打分：`core/signal/scorer.score_bars` + `compute_buy_stance`（模拟与回溯共用）。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List

# 与 docs/quant-ui.md · quant-concepts.md 一致
ACTIONS: List[Dict[str, Any]] = [
    {
        "id": "strategy",
        "order": 1,
        "label": "策略",
        "summary": "信号→决策规则（被验证的对象）",
        "ui": "量化面板 · 策略指纹",
        "apis": [
            "/api/signal/config/file",
            "/api/signal/config/diff-preview",
            "/api/quant/config",
        ],
        "services": [
            "quant.services.quant_service_config.QuantConfigMixin",
        ],
        "core": ["core.signal.config", "data/signal_config.json"],
    },
    {
        "id": "replay",
        "order": 2,
        "label": "回溯",
        "summary": "研究池 + Top-K 历史回测 + 横截面（历史卷）",
        "ui": "量化面板 · 历史验证卡",
        "apis": [
            "/api/watching/*",
            "/api/quant/portfolio-backtest",
            "/api/quant/portfolio-neutral-compare",
            "/api/quant/cross-section",
        ],
        "services": [
            "quant.services.quant_service_replay.QuantReplayMixin",
            "quant.services.quant_service_factors.QuantFactorMixin.run_cross_section",
            "quant.services.quant_service_ops.QuantOpsMixin（watching）",
        ],
        "core": [
            "core.watching_store",
            "core.backtest.topk_backtest",
            "core.signal.cross_section",
        ],
    },
    {
        "id": "follow",
        "order": 3,
        "label": "模拟",
        "summary": "纸面假钱持续记账（前瞻验证）",
        "ui": "模拟 Tab（执行）· 量化前瞻验证卡（桥接）",
        "apis": [
            "/api/paper/*",
            "/api/paper/rebalance",
            "/api/paper/t0",
            "/api/quant/t0-backtest",
            "/api/watching/refresh?sync_paper=true",
        ],
        "services": [
            "services.paper_service.PaperService",
            "quant.services.quant_service_follow.QuantFollowMixin",
        ],
        "core": ["core.paper", "core.paper_rebalance", "core.t0"],
    },
    {
        "id": "compare",
        "order": 4,
        "label": "联动",
        "summary": "模拟持仓与观察池/量化状态只读摘要",
        "ui": "模拟 · 持仓与观察重叠（可选）",
        "apis": [
            "/api/portfolio/quant-bridge",
        ],
        "services": [
            "quant.services.quant_service_compare.QuantCompareMixin",
            "quant.services.portfolio_quant_bridge",
        ],
        "core": ["core.paper", "core.watching_store"],
        "notes": [
            "纸面 vs TopK 对照已下线（口径不公平）",
            "portfolio-neutral-compare 属历史验证（规则变体对照），不是对照层",
            "skills/compare 是行情比价 Skill，与本动作无关",
        ],
    },
]


def action_map() -> Dict[str, Any]:
    return {
        "version": 2,
        "thesis": "基于信号开发决策策略，通过回溯与模拟验证有效性",
        "flow": ["观察/纸面", "策略", "回溯|模拟"],
        "ui_layout": {
            "pages": {
                "watching": "观察：长期名单",
                "paper": "纸面：只配假账户",
                "strategy": "策略：只看规则/diff",
                "replay": "回溯：只做历史实验",
                "follow": "模拟：假钱买卖",
                "quant": "枢纽：导览+进阶调参+运维",
            },
            "chat_tabs": "观察|模拟|回溯|回复",
            "topbar": "对话|观察|模拟|回溯",
        },
        "validation": {
            "replay": "Watching（考试范围）+ Top-K 回测引擎（历史卷）",
            "follow": "纸面账户（假钱前瞻）",
            "note": "两者测同一策略；对比看是否一致",
        },
        "shared_spine": [
            "core.signal.scorer.score_bars",
            "core.stance.compute_buy_stance",
            "data/signal_config.json",
        ],
        "actions": ACTIONS,
        "docs": [
            "docs/quant-ui.md",
            "docs/quant-concepts.md",
        ],
    }
