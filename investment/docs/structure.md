# 目录结构

[← 文档索引](README.md)

```
investment/
├── core/                        # 领域层（确定性逻辑，无 LLM/Handler）
│   ├── paths.py · env.py · numbers.py
│   ├── data_service.py          # 上层唯一读口（质量 / PIT 元数据）
│   ├── data_pit.py · data_coverage.py · store.py
│   ├── ports/                   # market · adapters · signal（skills 经 ports_bind 注入）
│   ├── signal/                  # scorer · factors · config · score_stock
│   ├── backtest/                # walk-forward · topk_backtest · strategies
│   ├── stance.py · advise.py · facts.py · position.py
│   ├── paper.py                 # 账本 IO / 五问 / signal_scan（再导出 exec）
│   ├── paper_exec.py            # 盯市 · 手动买卖 · 模拟加减仓
│   ├── paper_cycle.py           # 调仓日循环
│   ├── paper_rebalance.py · paper_sizing.py · paper_costs.py
│   ├── risk/checks.py           # 调仓前门禁
│   ├── strategy.py · strategy_monitor.py
│   ├── portfolio_optimize.py
│   ├── watching_store.py · watching_insights.py · watching_health.py
│   ├── job_progress.py · schedule_jobs.py · run_manifest.py
│   ├── decision_record.py · memory_store.py · feedback_suggest.py
│   ├── observation.py · order_prefill.py · alert_outbound.py
│   └── sentiment.py · t0/
├── quant/                       # 量化研究台
│   ├── services/                # QuantService、报告、持仓联动
│   ├── ops/                     # daily preset、健康检查
│   ├── research/                # 因子 IC、TopK 摘要、中性化对照
│   └── skill/                   # Agent quant(task=...) 引擎与 Handler
├── services/                    # 应用服务
│   ├── paper_service.py         # PaperService 组装
│   ├── paper_account.py · paper_jobs.py · paper_trades.py · paper_helpers.py
│   ├── chat_service.py · portfolio_service.py · daily_service.py
│   └── watching_service.py · platform_service.py · …
├── main.py                      # CLI
├── run_web.py                   # Web：uvicorn
├── web/
│   ├── app.py · deps.py · schemas.py · routers/
│   └── static/js/
│       ├── paper.js             # 模拟页编排
│       └── paper/fmt.js · chart.js
├── agent/                       # 编排层 + 认知层（正本）
├── skills/
│   ├── ports_bind.py            # 行情/信号适配器注册
│   ├── common/                  # quote_api · history（ports 实现侧）
│   └── <name>/                  # handler + engine + tool_config
├── research/                    # 薄 CLI（逻辑在 core/quant；读数经 DataService）
├── data/                        # JSON 状态 · store/ · jobs/paper.json
├── evals/ · tests/
└── docs/                        # 本目录
```

## 分层说明

| 层级 | 路径 | 职责 |
|------|------|------|
| 数据读口 | `core/data_service` → `core/ports` → `skills.ports_bind` | 业务/Skill/研究统一质量契约 |
| 共享领域 | `core/signal` · `core/backtest` · `core/paper*` · `core/risk` | live / 回测 / 纸面同一套规则 |
| 量化产品 | `quant/` | 研究台服务、运维 preset、因子对照、Agent Skill |
| 应用服务 | `services/` | Web/CLI 边界；纸面拆 account/jobs/trades |
| Agent 编排 | `agent/` · `skills/*` | LLM 路由、tool loop |

## Canonical 入口速查

| 模块 | 路径 |
|------|------|
| DataService | `core/data_service.py` |
| 行情端口 | `core/ports/market.py` · 绑定 `skills/ports_bind.py` |
| 因子打分 | `core/signal/scorer.py` |
| 回测引擎 | `core/backtest/engine.py` |
| 纸面账本 | `core/paper.py` + `paper_exec` + `paper_cycle` |
| 风控门禁 | `core/risk/checks.py` |
| 量化服务 | `quant/services/quant_service.py` |
| Agent quant Skill | `quant/skill/` · 注册 `skills/quant/` |
| 模拟账本 | `core/paper.py`（`paper.json`）；对话 position 默认读此 |
| 观察池 | `core/watching_store.py` |
| 研究 CLI | `research/*.py` |
| Job 轮询 | `GET /api/jobs/{name}`（纸面兼容 `/api/paper/job`） |

命名约定：产品「模拟」/ `/follow` = 内部 `paper`。详见 [framework-review.md](framework-review.md) · [architecture.md](architecture.md)（含 [技术栈](architecture.md#技术栈)）。
