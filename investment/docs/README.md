# docs

产品与工程文档：架构 · UI · 量化 · 运维。入口：README.md

- **[系统架构文档](system-architecture.md)** — 架构图 · 分层模块说明 · 代码组成（推荐入口）
- [工程结构轨 A0–A4](architecture-upgrade-a.md) — Bars SQLite · Job · BacktestService · 前端稳态
- [架构 · Service 命名约定](architecture.md#service-命名约定) — Application Service vs Domain Facade
- [日线/分钟线 SQLite 改造](sqlite-migration.md)
- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
- [predicted_score（ŷ）逻辑链路与执行链](predicted-score-chain.md) — 训练 · 打分 · 回测 · 复盘 · 纸面 · **§2.5 双层 ŷ_EOD+ŷ_τ** · **§2.6 预估周期 y_EOD·y_τ·y_ON**
- [Alpha / IC 补强（P0–P3）](alpha-ic-strengthen.md) — 超额分账 · 主 IC=截面 Spearman · 中性化/残差 y · regime 仓位闸
- [盘中剩余收益头 · 实时方案与落地规划](intraday-residual-score.md) — 事件先验 · rem 头 · 分阶段 R0–R3
- [决策时刻 τ 契约 · 双层 predicted_score · 分组目标升级](tau-contract-and-partition-upgrade.md) — ℱ_τ · 建模训练融合 · A/B 升级
