# architecture.md 拆分与瘦身 - 产品需求文档

## Overview
- **Summary**: 将 2944 行的 `docs/architecture.md` 按 Qlib component 模式拆分为「总览 + 组件文档 + 内部实现文档」三层结构，补充端到端全链路架构图与扩展指引，更新全部交叉引用。
- **Purpose**: 解决单文档过载、缺少全景图、缺少扩展指引、实施方案混入架构文档四个问题，使架构文档更易读、更易维护。
- **Target Users**: 本项目开发者、架构维护者、新成员入门读者。

## Goals
- 将 architecture.md 从 2944 行降至约 1300 行以内（总览定位）
- 数据层 / 策略层 / 风控层（含舆情）独立为 `docs/component/*.md`
- 框架梳理 / 工程结构轨 / SQLite 改造移入 `docs/internal/`
- 在 architecture.md 总览部分新增一张端到端全链路架构图
- 新增「如何扩展」一节（新增因子 / 新增策略 / 新增数据源）
- 全部跨文档锚点链接保持可用（43 处引用需逐一校验更新）

## Non-Goals
- 不重写任何技术内容（仅搬迁、拆分、补图、补指引、改链接）
- 不改动 docs/ 以外的任何文件
- 不新建 docs/component/ 之外的新分类
- 不调整 archive/ 已有文档

## Background & Context
- architecture.md 2944 行，涵盖：定位/设计原则/控制论/产品视角/分层架构/技术栈/组件详图/请求生命周期/执行链路/工具路由/接口设计/能力分层/分层模块说明/模块依赖/目录结构/数据层/策略层/风控层/舆情层/RL层/框架梳理/工程结构轨/SQLite改造。
- 对照微软 Qlib 文档（Introduction 框架图 + component/data + component/workflow + component/strategy），发现本仓库架构文档单文件过载、缺全景图、缺扩展指引。
- 已有跨文档引用：design-spine.md、quant.md、quant-ui.md、development.md、README.md 共引用 architecture.md 锚点 43 处。

## Functional Requirements
- **FR-1**: 拆分后 `docs/architecture.md` 仅保留：总览（定位/设计原则/控制论/产品视角/分层架构/Service命名约定）+ 技术栈 + 分层组件详图 + 请求生命周期 + 执行链路 + 工具路由 + 接口设计 + 能力分层 + 分层模块说明 + 模块依赖规则 + 目录结构 + 分层说明 + Canonical入口 + RL层 + 子目录README索引 + 新增端到端全链路图 + 新增扩展指引。
- **FR-2**: 新建 `docs/component/data.md`，承接原「数据层」全部内容（成熟模型五模块/本仓库对照/数据流/分钟线采集/存储选型/演进/相关代码速查）。
- **FR-3**: 新建 `docs/component/strategy.md`，承接原「策略层」全部内容。
- **FR-4**: 新建 `docs/component/risk.md`，承接原「风控模型」+「舆情与另类数据」全部内容。
- **FR-5**: 新建 `docs/internal/framework-review.md`，承接原「代码框架梳理与合理性分析」。
- **FR-6**: 新建 `docs/internal/engineering-track.md`，承接原「工程结构轨 A0–A4」。
- **FR-7**: 新建 `docs/internal/sqlite-migration.md`，承接原「日线/分钟线缓存 SQLite 改造方案」。
- **FR-8**: architecture.md 总览中新增端到端全链路架构图（数据→信号→决策→执行→反馈）。
- **FR-9**: architecture.md 新增「如何扩展」一节，含新增因子 / 新增策略 / 新增数据源三小节。
- **FR-10**: 更新 architecture.md 内部所有指向已迁移章节的锚点链接。
- **FR-11**: 更新外部文档（design-spine.md、quant.md、quant-ui.md、development.md、README.md）中指向已迁移章节的锚点链接。
- **FR-12**: 更新 docs/README.md 核心文档索引，新增 component/ 与 internal/ 条目。

## Non-Functional Requirements
- **NFR-1**: 搬迁内容不删减、不改写技术语义；标题锚点 ID 在新文件中保持不变。
- **NFR-2**: 所有 markdown 链接可解析（相对路径正确，锚点存在）。
- **NFR-3**: 新文件顶部含「← 文档索引」导航与指向 architecture.md 的回链。
- **NFR-4**: architecture.md 保留指向各 component/ 与 internal/ 的导航链接。

## Constraints
- **Technical**: 仅操作 `docs/` 目录下的 .md 文件；不改动代码。
- **Business**: 文档语言保持中文；技术术语保持现状。
- **Dependencies**: 拆分顺序必须先创建新文件，再从 architecture.md 删除对应段落，最后统一修链接。

## Assumptions
- 原 architecture.md 中各 `## ` 级标题的锚点 ID 在新文件中保持一致（markdown 自动生成）。
- 舆情层归入 risk.md 合理（舆情是风控输入）。
- RL 层保留在 architecture.md（跨切面远期视角，且被 quant.md/design-spine.md 频繁引用，保留可减少链接改动）。

## Acceptance Criteria

### AC-1: architecture.md 体量瘦身
- **Type**: `rule`
- **Given**: 拆分完成后的 architecture.md
- **When**: 统计行数
- **Then**: architecture.md 行数 ≤ 1400 行
- **Pass Condition**: `wc -l docs/architecture.md` ≤ 1400
- **Evidence**: 行数统计命令输出

### AC-2: 组件文档创建完整
- **Type**: `rule`
- **Given**: 拆分完成
- **When**: 检查 docs/component/ 与 docs/internal/ 目录
- **Then**: 存在 data.md、strategy.md、risk.md、framework-review.md、engineering-track.md、sqlite-migration.md 共 6 个文件
- **Pass Condition**: 6 个文件均存在且非空
- **Evidence**: `ls -la docs/component/ docs/internal/`

### AC-3: 端到端全链路图存在
- **Type**: `rule`
- **Given**: architecture.md
- **When**: 搜索 mermaid 全链路图
- **Then**: 存在一张覆盖「数据→信号→决策→执行→反馈」的 mermaid 流程图
- **Pass Condition**: grep 到包含数据/信号/决策/执行/反馈节点的 flowchart
- **Evidence**: grep 结果

### AC-4: 扩展指引存在
- **Type**: `rule`
- **Given**: architecture.md
- **When**: 搜索「如何扩展」章节
- **Then**: 存在含「新增因子」「新增策略」「新增数据源」三小节的章节
- **Pass Condition**: grep 到三个小节标题
- **Evidence**: grep 结果

### AC-5: 内部链接全部有效
- **Type**: `rule`
- **Given**: 拆分后的所有 .md 文件
- **When**: 校验所有 `architecture.md#xxx` 与 `component/xxx.md#yyy` 与 `internal/xxx.md#yyy` 链接
- **Then**: 无指向已删除段落的失效锚点
- **Pass Condition**: 所有链接目标文件与锚点均存在
- **Evidence**: 逐锚点校验记录

### AC-6: 外部文档链接更新
- **Type**: `rule`
- **Given**: design-spine.md、quant.md、quant-ui.md、development.md、README.md
- **When**: 检查其中 `architecture.md#数据层`、`architecture.md#策略层`、`architecture.md#风控层`、`architecture.md#舆情层`、`architecture.md#分钟线采集架构` 等链接
- **Then**: 已迁移章节的链接改为指向新文件（如 `component/data.md#数据层data-layer`）
- **Pass Condition**: 无遗留指向 architecture.md 已删除锚点的链接
- **Evidence**: grep 对比前后差异

### AC-7: 内容完整性（无丢失）
- **Type**: `rule`
- **Given**: 原 architecture.md 与拆分后的全部文件
- **When**: 对比关键技术段落
- **Then**: 原数据层/策略层/风控层/舆情层/框架梳理/工程轨/SQLite 的内容在新文件中完整保留
- **Pass Condition**: 新文件总行数 ≥ 原对应段落行数（允许导航行增减）
- **Evidence**: 行数对比

### AC-8: 导航一致性
- **Type**: `rule`
- **Given**: 所有新建文件
- **When**: 检查文件顶部
- **Then**: 每个新建文件第一行含「← 文档索引」指向 README.md，且含指向 architecture.md 的回链
- **Pass Condition**: 6 个新文件均满足
- **Evidence**: head 命令输出

### AC-9: 文档索引更新
- **Type**: `rule`
- **Given**: docs/README.md
- **When**: 检查核心文档索引
- **Then**: 索引中包含 component/ 与 internal/ 的条目说明
- **Pass Condition**: README.md 提及 component/data.md、component/strategy.md、component/risk.md
- **Evidence**: README.md 内容

### AC-10: 总览可读性
- **Type**: `rubric`
- **Dimension**: architecture.md 作为「总览」的信息密度与导航清晰度
- **Scale**: 1-5
- **Anchors**: 1 = 仍像大杂烩，无清晰总览定位；3 = 有总览骨架但导航弱；5 = 总览定位清晰，各组件入口明确，读者可快速定位
- **Pass Threshold**: >= 4
- **Evidence**: 通读 architecture.md 前 200 行与导航区

## Open Questions
- 无（拆分方案已在对比分析中确定并获用户批准）
