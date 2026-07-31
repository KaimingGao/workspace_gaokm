# quant/services

Web / daily / CLI 共用的量化服务层。

产品动作（与 [quant-ui.md](../../docs/quant-ui.md) 对齐）：**观察/纸面 → 策略 → 回溯|模拟**。
Web 路由：`/watching` `/paper` `/strategy` `/replay` `/follow`（`/quant` 枢纽）。  
机器可读映射：`action_map.py` · `GET /api/quant/actions`。  
**API URL 不变**（P94）。

## 动作归属

| 动作 | Mixin / 服务 | 典型 API |
|------|----------------|----------|
| 策略 | `QuantConfigMixin` | `/api/signal/config/*` · `/api/quant/config` |
| 观察 / 历史验证 | `QuantReplayMixin` + watching + `run_cross_section` | `/api/watching/*` · `portfolio-backtest` |
| 模拟 | `QuantFollowMixin` + `PaperService` | `/api/paper/*` · `t0-backtest` |
| 联动摘要 | `QuantCompareMixin` + bridge | `/api/portfolio/quant-bridge` |
| 进阶 | factors / ops | factor · export |

共享打分：`score_bars` + `compute_buy_stance`。

## 主要模块

| 模块 | 说明 |
|------|------|
| `quant_service.py` | **门面**：`QuantService` |
| `action_map.py` | 页面与动作归属图 |
| `quant_service_follow.py` | Mixin：T0 研究回测 |
| `quant_service_replay.py` | Mixin：Top-K 回测 · 中性化 |
| `quant_service_compare.py` | Mixin：持仓联动（只读 bridge） |
| `quant_service_config.py` | Mixin：配置 / diff |
| `quant_service_factors.py` | Mixin：因子 · 横截面 · 权重/阈值 |
| `quant_service_ops.py` | Mixin：watching · 日报 · 导出 · health |

- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)

