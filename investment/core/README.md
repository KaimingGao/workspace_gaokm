# core

共享领域层：确定性逻辑，无 LLM、无 HTTP Handler。

## 职责

- 路径与环境（`paths.py`、`env.py`）
- 行情事实聚合（`facts.py`）
- 买卖/观望 stance（`stance.py`、`advise.py`）
- 本地持仓与模拟账户（`paper.py` 账本 IO + `paper_exec` 盯市/成交 + `paper_cycle` 调仓流水线 + `paper_sizing` / `paper_costs`；行情经 `ports/` + DataService）
- 日线缓存（`store.py`）
- DataService 统一读口（`data_service.py`）
- 行情端口适配器（`ports/adapters.py`；默认由 `skills.ports_bind` 注入）
- 任务进度（`job_progress.py`；paper 槽落盘 `data/jobs/paper.json`）
- 观察舆情（`sentiment.py`：标题缓存 · 规则情绪 · 扫描告警；不进 stance）
- 信号打分与回测（见子目录）

## 子目录

| 目录 | 说明 |
|------|------|
| [signal/](signal/README.md) | 因子注册、单票/横截面评分 |
| [backtest/](backtest/README.md) | walk-forward 回测、组合、成本 |

## 相关文档

- [framework-review.md](../docs/framework-review.md)
- [structure.md](../docs/structure.md)
- [quant.md](../docs/quant.md)
- [data-layer.md](../docs/data-layer.md)
- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
