# Investment 文档

本目录为项目详细文档；根目录 [README.md](../README.md) 保留概览与快速上手。

**现行定位**：**策略验证**（量化研究 + 模拟账户）；**暂不涉及真实账户交易**。待策略验证成熟后再评估实盘（N6）。见 [design-spine · 产品边界](design-spine.md#产品边界现行)。

| 文档 | 内容 |
|------|------|
| [**design-spine.md**](design-spine.md) | **产品核心设计主轴**：本质/因果链 · **[产品北极星](design-spine.md#产品北极星)**（三项乘积）· [**能力地图 / N1–N6**](design-spine.md#能力地图六大模块) · 达成度 / P0–P2++ |
| [**roadmap.md**](roadmap.md) | 能力画像、Q1–Q5、**[N1–N6 / 子项目对照](roadmap.md#北极星实现路径n1n6)**、**北极星实现规划 P0–P3** |
| [**upgrade-refactor-plan.md**](upgrade-refactor-plan.md) | **已收口** R0–R5（北极星仪表 → PIT → 研究吞吐 → 风控 → 报告 → 工程债） |
| [**strategy-validation-upgrade.md**](strategy-validation-upgrade.md) | **策略验证 V0–V5**（已主干落地） |
| [**validation-strengthen.md**](validation-strengthen.md) | **S0–S4**：验证深化补强（PIT/截面 IC/验证包/闸门） |
| [**data-layer-strengthen.md**](data-layer-strengthen.md) | **D0–D4**：数据层深化补强（ann/源审计/复权/日历/DQ 中心） |
| [**evidence-strengthen.md**](evidence-strengthen.md) | **E0–E4**：北极星证据诚实（策略 KPI · 对齐 · IC PIT · 日历 · outcome 催办） |
| [**yhat-strengthen.md**](yhat-strengthen.md) | **Y0–Y5 已落地**：ŷ 生产硬化（口径 · 衰减 · 成本 · 暴露 · 拟合 · 样本） |
| [**strengthen-next.md**](strengthen-next.md) | **已收口 C/P/EP**：口径纠偏 · 组合换手/预算/归因 · 分组启用证据包 |
| [**weight-suggest-deepen.md**](weight-suggest-deepen.md) | **选股主轴（已落地）**：回归 ŷ · heuristic 仅 OOS 基线 · 分组 β |
| [**topk-backtest-upgrade.md**](topk-backtest-upgrade.md) | **TopK 回测**：T0–T16 已收口；**[T17 TTL可配·过期硬拦](topk-backtest-upgrade.md#11j-第十一程--t17-ttl-可配--过期硬拦已落地)** |
| [**n6-live-gate.md**](n6-live-gate.md) | N6 实盘准入备忘（闸门通过前不写 OMS 代码） |
| [structure.md](structure.md) | 仓库目录与 canonical 模块路径 |
| [**framework-review.md**](framework-review.md) | **代码框架梳理与合理性分析**：分层、主路径、债务分级与整改顺序 |
| [architecture.md](architecture.md) | 控制论闭环、工程分层、**[技术栈](architecture.md#技术栈)**、registry |
| [data-layer.md](data-layer.md) | **数据层**：采集/清洗/存储/服务/监控 · **[JSON vs 数据库选型](data-layer.md#存储选型为何是-json何时才上数据库)** · 演进 |
| [strategy-layer.md](strategy-layer.md) | **策略层**：选股择时/仓位/风控 · 输入输出 · 设计文档模板 |
| [risk-layer.md](risk-layer.md) | **风控模型**：风险因子 · Alpha×Risk · 现状与演进 |
| [rl-layer.md](rl-layer.md) | **强化学习视角**：Policy/Reward/Env 映射 · 奖励函数翻译 · 边界 |
| [sentiment-layer.md](sentiment-layer.md) | **舆情/另类数据**：新闻→风险分 · 与 news Skill 对照 · 演进 |
| [skills.md](skills.md) | Skill 详解（与 registry 13 工具对齐）、买入决策与示例回复 |
| [getting-started.md](getting-started.md) | 环境、安装、CLI/Web、使用示例 |
| [development.md](development.md) | 测试、evals、扩展 Skill、限制与数据源 |
| [quant-concepts.md](quant-concepts.md) | **入门概念**：信号→策略→验证；Watching/纸面；**涨跌归因与不强因子边界**；测试类型 |
| [quant.md](quant.md) | 量化原理：因子 → stance → 回测 → 纸面；[纸面是什么](quant.md#纸面是什么给小白)；[ML 视角](quant.md#机器学习视角如何理解量化) |
| [quant-ui.md](quant-ui.md) | **Web 主路径说明书**：仪表盘 · 观察 · 模拟 · 回溯 · **[用户心智与验证闭环](quant-ui.md#用户心智与验证闭环)** |
| [**quant-ui-standard.md**](quant-ui-standard.md) | **改 UI 契约**：IA · 页面契约 · 组件白名单 · `ASSET_V` · 验收清单 |
| [**quant-ui-gap.md**](quant-ui-gap.md) | **Web UI 差距分析**：相对专业量化终端（边界外 / 路径内缺口 / 取向差异） |
| [**quant-ui-upgrade.md**](quant-ui-upgrade.md) | **Web UI 全面升级方案**：W0–W4 工作流 · 验收 · 与后端咬合 · 下一刀 |
| [quant-ui-refactor-plan.md](quant-ui-refactor-plan.md) | **Web UI 壳层简史**：六模块壳 · P0–P1；详细升级见 upgrade |
| [quant-upgrade.md](quant-upgrade.md) | **历史归档**：P6～P93 交付流水账（非现行设计主文档） |
| [quant-summary.md](quant-summary.md) | P6～P26 一页总览与验收命令 |
| [quant-ops.md](quant-ops.md) | 定时任务 preset、cron/launchd、报告归档与分享 |

建议阅读顺序：**[design-spine](design-spine.md)（产品主轴 · [因果链](design-spine.md#因果链已发生--影响估计--动作)）→ [framework-review](framework-review.md)（代码框架与债务）→ structure → architecture → getting-started → skills**；做量化时再读 **[quant-concepts.md](quant-concepts.md)** → **[quant-ui.md](quant-ui.md)（每页一事）** → 改界面前读 **[quant-ui-standard.md](quant-ui-standard.md)**；对照专业终端读 **[quant-ui-gap.md](quant-ui-gap.md)** → Web 排期读 **[quant-ui-upgrade.md](quant-ui-upgrade.md)**；**已收口升级**读 **[upgrade-refactor-plan.md](upgrade-refactor-plan.md)**（R0–R5）→ **[strategy-validation-upgrade.md](strategy-validation-upgrade.md)**（V0–V5）→ **[validation-strengthen.md](validation-strengthen.md)**（S0–S4）→ **[data-layer-strengthen.md](data-layer-strengthen.md)**（D0–D4）→ [evidence-strengthen.md](evidence-strengthen.md)（E0–E4）→ [strengthen-next.md](strengthen-next.md)（C/P/EP）→ **现行下一程 [yhat-strengthen.md](yhat-strengthen.md)**（Y0–Y5）→ TopK 读 **[topk-backtest-upgrade.md](topk-backtest-upgrade.md)**，然后 **quant / [data-layer](data-layer.md) / [strategy-layer](strategy-layer.md) / [risk-layer](risk-layer.md) / [rl-layer](rl-layer.md) / [sentiment-layer](sentiment-layer.md) / development / roadmap**。

各代码子目录另有 **README.md**（职责与入口速查）；索引见 [architecture.md#子目录-readme-索引](architecture.md#子目录-readme-索引)，API：`GET /api/readme-index` · `GET /api/readme?dir=`。

## 相关文档

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
