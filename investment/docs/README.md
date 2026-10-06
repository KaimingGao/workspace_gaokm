# docs

产品与工程文档。核心文档 + `component/` 组件文档 + `internal/` 内部实现文档 + `archive/` 历史归档。

- [架构总览 · 子目录索引](architecture.md#子目录-readme-索引)

---

## 核心文档索引（推荐阅读顺序）

0. **[手把手：从 clone 到跑通回测](getting-started.md)** — 装环境 · 小观察名单 · 拉日线/分钟线 · 拟合并启用研究套 · 第一次调仓回测
1. **[产品介绍](product-intro.md)** — 产品概述 · Web 面板总览 · 选股/调仓/做T 速览 · 关键代码索引
2. **[系统架构总览](architecture.md)** — 定位 · 设计原则 · 控制论闭环 · 分层架构 · 六边形 Ports & Adapters · 技术栈 · 分层模块说明 · 端到端全链路图 · 如何扩展 · RL 视角
3. **[产品核心设计主轴与路线图](design-spine.md)** — 本质与因果链 · Ensemble 预估方法论 · 产品北极星 · 能力地图 · N1–N6 路径 · 决策链路 · N6 实盘准入
4. **[量化原理、ŷ 全链路与运维](quant.md)** — 入门概念 · 因子 ŷ / stance / 回测 / 纸面原理 · 训练/打分/回测/复盘链 · 双层 ŷ · 运维 preset & cron
5. **[策略调仓逻辑](rebalance-logic.md)** — 横截面调仓 · dual_y 买卖闸 · 卖出腿/卖后门禁/买入腿 · path_matrix · 风控体系 · 参数速查
6. **[底仓做 T 逻辑](t0-logic.md)** — v6 收盘带宽选腿 · dual_y 多层 ŷ 准入 · Leg1/Leg2 执行 · 止损/追价/强平 · 参数速查
7. **[量化研究台（Web）](quant-ui.md)** — 说明书 · 页面契约与验收清单 · 相对专业终端差距分析 · W0–W5 升级方案
8. **[开发与入门手册](development.md)** — 环境安装 · 操作入门 · 运行测试与 evals · prompts 说明 · Skill 详解

## 组件文档（component/）

架构各层的详细设计，由 [architecture.md](architecture.md#组件文档导航) 导航。

| 文档 | 内容 |
|------|------|
| [数据层](component/data.md) | 采集/清洗/存储/服务/监控五模块 · 分钟线采集架构 · 存储选型 · PIT 边界 |
| [策略层](component/strategy.md) | 选股/择时/仓位/风控 · 策略设计文档模板 · 默认短线策略 |
| [风控层](component/risk.md) | Alpha×Risk 闭环 · 风控因子 · 舆情与另类数据 |
| [RL 视角](component/rl.md) | Policy/Reward/Env 映射 · 与现有栈衔接（远期） |

## 内部实现文档（internal/）

工程实施细节与历史方案，非架构必读。

| 文档 | 内容 |
|------|------|
| [框架梳理](internal/framework-review.md) | 代码框架债务台账 · 已收口/设计保留/明确不做 |
| [工程结构轨](internal/engineering-track.md) | A0–A4 契约冻结 → Bars SQLite → Job 硬化 → 门面 → 前端稳态 |
| [SQLite 迁移](internal/sqlite-migration.md) | 日线/分钟线缓存 SQLite WAL 改造方案（已落地 A1） |

## 历史归档（archive/）

已收口的 strengthen / upgrade / refactor 计划文档（R0–R5、V 轨、UI 简史等）。
