# docs

产品与工程文档。核心文档 + `component/` 组件文档 + `internal/` 内部实现文档。

- [架构总览 · 子目录索引](architecture.md#子目录-readme-索引)

**职责速记**：`architecture` = 分层拓扑；`design-spine` = 产品主轴 / 北极星 / N 轨；`quant.md` = **ŷ 契约真源**；`quant-ops.md` = preset / cron；`rebalance` / `t0` = 产品规则；`quant-ui` = Web 契约。

---

## 核心文档索引（推荐阅读顺序）

0. **[手把手：从 clone 到跑通回测](getting-started.md)** — 装环境 · 小观察名单 · 拉日线/分钟线 · 拟合并启用研究套 · 第一次调仓回测
1. **[产品介绍](product-intro.md)** — 产品概述 · Web 面板总览 · 选股/调仓/做T 速览 · 关键代码索引
2. **[系统架构总览](architecture.md)** — 定位 · 设计原则 · 控制论闭环 · 分层架构 · 六边形 Ports & Adapters · 技术栈 · 端到端全链路 · 如何扩展
3. **[产品核心设计主轴与路线图](design-spine.md)** — 因果链 · Ensemble · 产品北极星 · 能力地图 · N1–N6 · N6 实盘准入
4. **[量化原理与 ŷ 全链路](quant.md)** — 契约真源 · 双层 ŷ · 训练/打分/回测/纸面 · Live 旁路枢纽
5. **[量化运维](quant-ops.md)** — preset · cron / launchd · 报告归档 · watching 文件 API · 信号配置只读
6. **[策略调仓逻辑](rebalance-logic.md)** — rank_lots · fuse · 卖/买腿 · path_matrix · 风控 · 参数速查
7. **[底仓做 T 逻辑](t0-logic.md)** — 纯 ŷ_τw 选向/金额 · Leg1/Leg2 · 止损/追价/强平
8. **[量化研究台（Web）](quant-ui.md)** — 说明书 · 页面契约 · 差距分析 · W0–W5 升级方案
9. **[开发与入门手册](development.md)** — 环境安装 · 测试与 evals · prompts · Skill 详解

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
| [工程结构轨](internal/engineering-track.md) | A0–A4 · B0–B4 · **C0–C3 / C1.x 已落地**（限额 SSOT · validate_paper · kelly 单位） |
| [SQLite 迁移](internal/sqlite-migration.md) | 日线/分钟线缓存 SQLite WAL 改造方案（已落地 A1） |
| [行情更新、缓存与预处理](internal/bars-update-cache.md) | 日线/5m 写仓与回测读仓、缓存分层、清洗与面板指纹、四处已知缝 |
| [Feature Lake / DayCube 方案](internal/feature-lake-daycube.md) | L1/L2：开盘 M0–M4 + ŷ_τ\* 按钟 **H0–H5**；日线物化 open · 分钟线 Ready 后物化 τ |
| [DayCube 宇宙键](internal/daycube-universe-key.md) | 物化与回测的 `universe_hash`：完整名单做键、有行代码进矩阵；旧 798 目录与重跑 |
| [Live 早开盘今开截面](internal/live-open-pit-rebalance.md) | live 开盘窗误用昨日立方的根因、open-pit 方案、z/耗时/行情频率与验收 |
| [日线契约修补规划](internal/bars-update-plan.md) | 四处缝的改造顺序：记账、面板指纹、成交额列、前复权重叠日替换 |
| [AkShare vs Tushare 日线对比](internal/akshare-tushare-daily-compare.md) | 本地日线仓与 Tushare daily 对齐审计（复权标签 / 量单位） |
