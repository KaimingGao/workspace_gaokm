# Web UI · 相对专业量化终端的差距分析

[← 文档索引](README.md) · 说明书 [quant-ui.md](quant-ui.md) · 契约 [quant-ui-standard.md](quant-ui-standard.md) · **升级方案 [quant-ui-upgrade.md](quant-ui-upgrade.md)** · 壳层简史 [quant-ui-refactor-plan.md](quant-ui-refactor-plan.md) · 产品主轴 [design-spine.md](design-spine.md)

**评估日期**：2026-07-29  
**对照对象**：专业量化交易系统 Web UI / 功能设计（研究→回测→实盘 OMS→风控全链路；高密度、低延迟、可停靠分屏）  
**本文用途**：把「机构交易终端满分画像」与本仓库 **研究台 + 模拟账户** 对照，区分 **边界外（不做）** / **边界内缺口（该补）** / **体验取向差异（改前先改契约）**。

产品能力达成度见 [design-spine · 产品北极星](design-spine.md#产品北极星) · [能力地图](design-spine.md#能力地图六大模块) · [达成度评估](design-spine.md#达成度评估2026-07)。本文聚焦 **Web UI/UX 与前端能力面**。

---

## 0. 读结论前的定位差

| 维度 | 专业方案 | 本仓库 |
|------|----------|--------|
| 闭环终点 | 实盘 OMS / EMS | **策略验证**：纸面账本；**现行不接券商**；成熟后 N6 |
| 主用户 | Quant / Trader / Risk 三角色 | 单人研究员（研究 · 模拟 · 运维合一） |
| UI 哲学 | 高密度、暗色盯盘、可停靠分屏 | 报告块序、浅色默认、**明确不学 Bloomberg 密度**（见 [quant-ui-standard](quant-ui-standard.md)） |
| 技术栈 | React + WebSocket + AG Grid + Dock | 多页静态壳 + `fetch` 轮询 + 自绘 canvas |

对照时拆两层：

1. **边界外** — 按 OMS/盘口打分接近 0 是预期，不是实现疏漏。  
2. **边界内** — 研究/模拟 Web 体验仍偏「工具页」，离聚宽 / QuantConnect 级研究台还有一档。

一句话：IA（观察 / 模拟 / 回溯 / 策略）与纸面闭环已成形；**W0–W5** + **R0–R5** 已补图表、报告、风控、虚拟表、北极星。路径内下一程见 **[strategy-validation-upgrade.md](strategy-validation-upgrade.md)**（V0–V5）。OMS/盘口保持空白直至成熟闸门。

> **进度注（2026-07-29）**：落地以 [quant-ui-upgrade.md](quant-ui-upgrade.md)（W0–W5 ✅）、[upgrade-refactor-plan.md](upgrade-refactor-plan.md)（R0–R5 ✅）与 **[strategy-validation-upgrade.md](strategy-validation-upgrade.md)**（V0–V5 待开工）为准。

---

## 1. 六大功能模块对照

### 1.1 策略研发工作台（Quant IDE）— 弱

| 专业能力 | 本地现状 | 判断 |
|----------|----------|------|
| Monaco / 在线写策略 | 无；策略靠配置 / 规格卡 | **路径内缺口**（[refactor P2](quant-ui-refactor-plan.md) 已列 Monaco） |
| Notebook 探索 | 无 | 缺口 |
| 数据字典 / API 插入 | 无；AI 旁路代偿一部分 | 缺口 |
| 因子 / 策略规格 | `/strategy` 只读 IC/权重 + promote | 有骨架，偏「看卡」不是「写策略」 |

**缺陷**：研究入口是「表 + 调参按钮」，不是 IDE；策略迭代依赖改配置 / 离线脚本，Web 闭环断在「写代码」。

### 1.2 回测与历史绩效 — 中等偏弱

| 专业能力 | 本地现状 | 判断 |
|----------|----------|------|
| 参数网格 / 热力图 | 基本无 | 缺口 |
| 年化 / 夏普 / 回撤 | `/replay` 指标卡 + 曲线 | 有 |
| Brinson / 因子归因 | 简化选股超额，非完整 Brinson | **深度不够** |
| Tick / 分钟逐笔回放 | 无；日线级成交样本 | 缺口（日线产品可接受） |
| 滑点 / 手续费拆解 | 成本模型有，UI 偏摘要 | UI 不够专业报告级 |

**缺陷**：能「跑一轮看结果」，缺机构报告里的**归因矩阵、参数曲面、信号–成交对照回放**。

### 1.3 实盘 OMS / EMS — 故意缺失（边界外）

TWAP / VWAP、订单状态机、多账户矩阵、盘口下单 —— **全部不在产品范围**。本地是「加减仓确认 + 日更调仓预演」，不是执行管理系统。

**不要按 OMS 打分**；能力地图中本块接近 0 是**策略验证阶段**预期。真·实盘见 [design-spine N6](design-spine.md#北极星实现路径n1n6)（验证成熟后另立项；不计入 [产品北极星](design-spine.md#产品北极星) 分子）。

### 1.4 数据中心 — 薄

| 专业能力 | 本地现状 | 判断 |
|----------|----------|------|
| 多源（Tick / 财务 / 舆情…） | 日线为主 + 舆情标题；财务 PIT 仍弱 | 数据深度不足 |
| 数据质量看板 | `data_quality` / `fallback` 进五问文案 | **有信号、无独立监控页** |
| 缺失率 / 修复记录可视化 | 无 | 缺口 |

**缺陷**：`/watching` 是「观察名单工作台」，不是专业 Data Center；侧栏命名对齐了，能力未对齐。

### 1.5 风控管理 — 有规则、缺终端

| 专业能力 | 本地现状 | 判断 |
|----------|----------|------|
| 事前拦截 | 限额 / 涨跌停近似 / 黑名单类后端逻辑 | 有 |
| 事中敞口（行业 / Beta / 集中度） | 预算 lite、行业告警、五问展示 | 浅 |
| 阈值配置 UI / 强平流 | 策略限额多只读；无独立风控台 | **UI 缺位** |

**缺陷**：风控结果散落在待办 / 五问 / 调仓报告，没有 Risk/Ops「敞口仪表 + 阈值编辑 + 审计列表」专页。

### 1.6 系统监控与运维 — 运维级偏弱

| 专业能力 | 本地现状 | 判断 |
|----------|----------|------|
| 网关 / 引擎 CPU · 延迟 | 无 | 边界外 / 远期 |
| 健康 / 调度 / 告警 | daily health、schedule、出站告警 | 有 |
| 全链路合规审计 UI | DecisionRecord / feedback / promote | 有碎片，无审计工作台 |
| 断线红 Banner / 自动重连 | REST 轮询；无 WebSocket；网络降级弱 | **实时 UX 缺口** |

---

## 2. UI/UX 相对专业终端

按「效率至上、信息高密度、抗疲劳、低延迟」对照：

| 维度 | 专业终端 | 本地 | 结论 |
|------|----------|------|------|
| 视觉 | 暗色为主、去装饰、硬边 | 浅色默认、报告感、密度克制 | 对研究员合理；对盯盘不专业（**选型，非疏漏**） |
| 布局 | Docking / 分屏 / 布局方案 | 固定六模块单主列 | 无法一屏并列名单 + K 线 + 持仓 + 日志 |
| 密度 | 11–12px、紧凑/宽松切换 | 报告块序、刻意克制 | 与标准「不学 Bloomberg」一致 |
| 键盘 | 全局热键 + Command Palette | 主要 ⌘K（AI） | 效率上限是点按钮 |
| 图表 | 十字光标、指标、画线 | 自绘 canvas 日线 | Lightweight Charts 未落地（P2） |
| 盘口 / 逐笔 | Level2、Time & Sales、闪烁 | 无 | 边界外 |
| 表格 | AG Grid 级虚拟滚动 / 行内编辑 | 自研 `.quant-weight-table` | 大数据量与复杂交互弱 |
| 实时 | WebSocket 局部重绘、节流 | ~90s 轮询 | 研究够用，非交易终端 |
| 权限合规 UX | RBAC、脱敏、二次确认体系 | 单用户本地；调仓确认有 | 非机构权限模型 |

---

## 3. 前端技术栈差距

| 专业推荐 | 本地 | 后果 |
|----------|------|------|
| React + TypeScript | 多页 HTML partial + 原生 JS | 复杂状态 / 布局难做 |
| Zustand + WebSocket | `fetch` + 局部全局状态 | 难做高频局部刷新 |
| Lightweight Charts | 自绘 canvas | K 线交互天花板低 |
| AG Grid / TanStack Table | 自研表 | 虚拟列表、行内编辑弱 |
| Dockview / Golden Layout | 无 | 无专业分屏 |
| Web Worker | 基本无 | 重计算易卡主线程 |

[quant-ui-refactor-plan P2](quant-ui-refactor-plan.md) 已写明 Monaco · Lightweight Charts · 可选 React · 实盘（边界外）——团队已知，尚未切刀。

---

## 4. 角色场景覆盖

| 角色 | 专业诉求 | 本地覆盖 |
|------|----------|----------|
| Quant | IDE + 归因 + Notebook | **部分**：回测 / 因子卡 / AI 解读；缺写码与深度归因 |
| Trader | OMS + 盘口 + 快捷键 | **几乎不覆盖**（纸面加减仓 ≠ 交易终端） |
| Risk / Ops | 全局风控台 + 审计 | **碎片覆盖**：待办 / 五问 / 健康；缺专页 |

实际服务对象是「个人量化研究员的研究–模拟闭环」，不是机构三角色栈。

---

## 5. 缺陷分级与行动指引

### A. 现行边界外（策略验证阶段不当缺陷修）

OMS / EMS、Algo 单、Level2、多账户、网关延迟监控、真·实盘——**待策略验证成熟后再评估 N6**。

### B. 边界内、高价值缺口（研究台该补）

1. **专业图表** — ✅ Lightweight Charts（十字线、MA、回测叠加）  
2. **回测报告专业化** — ✅ 成本/归因/参数扫描 · Brinson lite · 信号成交对照  
3. **数据质量独立可见** — ✅ 观察页折叠看板  
4. **风控 / 审计聚合** — ✅ 策略折叠 + 暴露矩阵 + outcome 标注 + 平台审计  
5. **实时与降级** — ✅ Banner + `/ws/live` + last-known  
6. **表格与 `paper.js` 工程债** — ✅ 虚拟表预算 + holdings 岛拆分（R5）  
7. **回测–纸面拟合** — ✅ Corr/TE 北极星（R0）；样本持续积累  
8. **Quant IDE** — Monaco 可编辑草稿 ✅；Notebook 仍缺  

### C. 体验取向差异（改前先改契约）

暗色盯盘密度 vs 浅色报告块序 — **默认仍报告感**；W4 已开：研究 Dock、紧凑密度开关、React 岛（非全站 SPA）。  
OMS 级盘口布局仍禁止。

---

## 6. 与相关文档的分工

| 文档 | 内容 |
|------|------|
| [design-spine.md](design-spine.md) | [产品北极星](design-spine.md#产品北极星) · 能力地图与后端达成度 |
| [roadmap.md](roadmap.md) | N1–N6 / P0–P3 工程节奏 |
| [upgrade-refactor-plan.md](upgrade-refactor-plan.md) | **已收口** R0–R5 |
| [strategy-validation-upgrade.md](strategy-validation-upgrade.md) | **现行下一程** V0–V5（策略验证成熟度 · N6 闸门） |
| [quant-ui-standard.md](quant-ui-standard.md) | **现行**改 UI 契约（密度克制、白名单） |
| [quant-ui-upgrade.md](quant-ui-upgrade.md) | **怎么补**：W0–W4 工作流、验收、闸门、下一刀 |
| [quant-ui-refactor-plan.md](quant-ui-refactor-plan.md) | 壳 / AI 已完成简史 |
| **本文** | 相对专业交易终端的 **UI/功能差距与分级** |
| [framework-review.md](framework-review.md) | 代码分层与工程债（非 UI 竞品对照） |

改 UI 时：先看标准是否仍坚持「研究台报告感」；若坚持，优先消化 **§5.B**，忽略 **§5.A**，勿用 **§5.C** 推翻契约。落地排期见 **[quant-ui-upgrade.md](quant-ui-upgrade.md)**。
